"""Customised NFC card orders (Sections 7 and 8)."""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django_countries.fields import CountryField

from apps.core.utils import keep_name


class PriceTier(models.Model):
    """Per-card NFC prices by quantity (7.1, NFP-01).

    A ``flat`` tier charges every card in the order at its unit price. An
    ``incremental`` tier (the "more than 20" row) charges the total of the
    tier below at its top quantity, plus its unit price for each extra card.
    """

    FLAT = "flat"
    INCREMENTAL = "incremental"

    min_quantity = models.PositiveIntegerField()
    max_quantity = models.PositiveIntegerField(null=True, blank=True)
    unit_price_minor = models.PositiveIntegerField()
    mode = models.CharField(
        max_length=12, choices=[(FLAT, "Every card at this price"), (INCREMENTAL, "Extra cards above the previous tier")],
        default=FLAT,
    )

    class Meta:
        ordering = ["min_quantity"]

    def __str__(self):
        top = self.max_quantity or "+"
        return f"{self.min_quantity}–{top}"

    @property
    def range_label(self):
        if self.max_quantity is None:
            return f"More than {self.min_quantity - 1} cards"
        if self.min_quantity == self.max_quantity:
            return f"{self.min_quantity} card{'s' if self.min_quantity != 1 else ''}"
        return f"{self.min_quantity}–{self.max_quantity} cards"


class DeliveryZone(models.Model):
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=200, blank=True)
    fee_minor = models.PositiveIntegerField(default=0)
    eta = models.CharField(max_length=80, help_text="For example '1–2 working days after dispatch'.")
    is_pickup = models.BooleanField(default=False)
    is_international = models.BooleanField(
        default=False,
        help_text="Shipping outside Ghana. The customer pays this fee for shipping and delivery; "
        "import duties in their country are theirs to pay.",
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["is_international", "sort_order", "fee_minor"]

    def __str__(self):
        return self.name

    @property
    def region_label(self):
        return "Outside Ghana" if self.is_international else "In Ghana"


class NFCOrder(models.Model):
    PENDING_PAYMENT = "pending_payment"
    PAYMENT_CONFIRMED = "payment_confirmed"
    DESIGN_PENDING = "design_pending"
    DESIGN_IN_PROGRESS = "design_in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    REVISION_REQUESTED = "revision_requested"
    ON_HOLD = "on_hold"
    APPROVED = "approved"
    PRINTING = "printing"
    ENCODING_QC = "encoding_qc"
    READY_FOR_DELIVERY = "ready_for_delivery"
    READY_FOR_PICKUP = "ready_for_pickup"
    DISPATCHED = "dispatched"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"

    STATUSES = [
        (PENDING_PAYMENT, "Pending payment"),
        (PAYMENT_CONFIRMED, "Payment confirmed"),
        (DESIGN_PENDING, "Design pending"),
        (DESIGN_IN_PROGRESS, "Design in progress"),
        (AWAITING_APPROVAL, "Awaiting your approval"),
        (REVISION_REQUESTED, "Revision requested"),
        (ON_HOLD, "On hold"),
        (APPROVED, "Design approved"),
        (PRINTING, "Printing"),
        (ENCODING_QC, "Encoding and quality check"),
        (READY_FOR_DELIVERY, "Ready for delivery"),
        (READY_FOR_PICKUP, "Ready for pickup"),
        (DISPATCHED, "Dispatched"),
        (DELIVERED, "Delivered"),
        (CANCELLED, "Cancelled"),
        (REFUNDED, "Refunded"),
    ]

    # Where staff may move an order next. Customer actions (approve, request
    # changes, cancel) have their own views and rules.
    TRANSITIONS = {
        PENDING_PAYMENT: [CANCELLED],
        PAYMENT_CONFIRMED: [DESIGN_PENDING, DESIGN_IN_PROGRESS, ON_HOLD, CANCELLED],
        DESIGN_PENDING: [DESIGN_IN_PROGRESS, ON_HOLD, CANCELLED],
        DESIGN_IN_PROGRESS: [ON_HOLD, CANCELLED],
        AWAITING_APPROVAL: [DESIGN_IN_PROGRESS, ON_HOLD, CANCELLED],
        REVISION_REQUESTED: [DESIGN_IN_PROGRESS, ON_HOLD, CANCELLED],
        ON_HOLD: [DESIGN_IN_PROGRESS, AWAITING_APPROVAL, PRINTING, CANCELLED],
        APPROVED: [PRINTING, ON_HOLD],
        PRINTING: [ENCODING_QC],
        ENCODING_QC: [READY_FOR_DELIVERY, READY_FOR_PICKUP],
        READY_FOR_DELIVERY: [DISPATCHED],
        READY_FOR_PICKUP: [DELIVERED],
        DISPATCHED: [DELIVERED],
        DELIVERED: [],
        CANCELLED: [REFUNDED],
        REFUNDED: [],
    }
    DESIGN_STARTED = {DESIGN_IN_PROGRESS, AWAITING_APPROVAL, REVISION_REQUESTED, APPROVED}
    PRINTING_STARTED = {PRINTING, ENCODING_QC, READY_FOR_DELIVERY, READY_FOR_PICKUP, DISPATCHED, DELIVERED}
    OPEN_STATUSES = {
        PAYMENT_CONFIRMED, DESIGN_PENDING, DESIGN_IN_PROGRESS, AWAITING_APPROVAL,
        REVISION_REQUESTED, ON_HOLD, APPROVED, PRINTING, ENCODING_QC,
        READY_FOR_DELIVERY, READY_FOR_PICKUP, DISPATCHED,
    }

    order_number = models.CharField(max_length=32, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="nfc_orders")
    status = models.CharField(max_length=24, choices=STATUSES, default=PENDING_PAYMENT, db_index=True)

    quantity = models.PositiveIntegerField()
    cards_minor = models.PositiveIntegerField(help_text="Card price for the whole quantity.")
    delivery_fee_minor = models.PositiveIntegerField(default=0)
    plan_minor = models.PositiveIntegerField(default=0, help_text="Subscription bought in the same checkout (NFL-03).")
    discount_minor = models.PositiveIntegerField(default=0)
    tax_minor = models.PositiveIntegerField(default=0)
    total_minor = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="GHS")
    bundled_plan = models.ForeignKey(
        "billing.Plan", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    # Printed-card details (7.3), pre-filled from the digital card (NFL-02).
    full_name = models.CharField(max_length=120)
    business_name = models.CharField(max_length=160, blank=True)
    position = models.CharField(max_length=120, blank=True)
    email = models.EmailField()
    phone = models.CharField(max_length=32)
    website = models.CharField(max_length=255, blank=True)
    logo = models.FileField(upload_to=keep_name, blank=True)
    reference_design = models.FileField(upload_to=keep_name, blank=True)
    design_description = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    # Delivery (7.4).
    delivery_zone = models.ForeignKey(DeliveryZone, null=True, blank=True, on_delete=models.PROTECT)
    recipient_name = models.CharField(max_length=120, blank=True)
    recipient_phone = models.CharField(max_length=32, blank=True)
    delivery_address = models.CharField(max_length=255, blank=True)
    delivery_city = models.CharField(max_length=120, blank=True)
    delivery_postcode = models.CharField(max_length=20, blank=True)
    delivery_country = CountryField(blank=True, default="GH")
    delivery_gps = models.CharField(max_length=16, blank=True)
    landmark = models.CharField(max_length=200, blank=True)
    courier_name = models.CharField(max_length=80, blank=True)
    tracking_number = models.CharField(max_length=80, blank=True)

    revision_rounds = models.PositiveSmallIntegerField(default=0)
    accepted_refund_policy_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    awaiting_since = models.DateTimeField(null=True, blank=True)
    qc_android_ok = models.BooleanField(default=False)
    qc_iphone_ok = models.BooleanField(default=False)
    qc_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    qc_at = models.DateTimeField(null=True, blank=True)
    tags_locked = models.BooleanField(default=False)
    admin_notified_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True)

    # Carried over from the Flask site for orders placed there.
    legacy_order_type = models.CharField(max_length=32, blank=True)
    legacy_replacement_reason = models.CharField(max_length=64, blank=True)
    legacy_id = models.IntegerField(unique=True, null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.order_number

    @property
    def is_paid(self):
        return self.paid_at is not None

    @property
    def is_pickup(self):
        return bool(self.delivery_zone and self.delivery_zone.is_pickup)

    @property
    def is_international(self):
        return bool(self.delivery_zone and self.delivery_zone.is_international)

    @property
    def shipping_address_lines(self):
        """The address as it goes on the parcel label."""
        lines = [self.recipient_name, self.delivery_address]
        city = " ".join(part for part in (self.delivery_city, self.delivery_postcode) if part)
        if city:
            lines.append(city)
        if self.is_international and self.delivery_country:
            lines.append(self.delivery_country.name)
        elif self.delivery_gps:
            lines.append(self.delivery_gps)
        if self.landmark:
            lines.append(self.landmark)
        return [line for line in lines if line]

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    @property
    def customer_can_cancel(self):
        """RFD-01..03: cancellable until printing starts."""
        return self.is_paid and self.status not in self.PRINTING_STARTED | {self.CANCELLED, self.REFUNDED}

    @property
    def refundable_minor(self):
        """What a cancellation would refund now (RFD-01..03)."""
        if not self.is_paid or self.status in self.PRINTING_STARTED:
            return 0
        if self.status in self.DESIGN_STARTED or self.revision_rounds or self.proofs.exists():
            return self.cards_minor // 2
        return self.total_minor - self.plan_minor

    @property
    def latest_proof(self):
        return self.proofs.order_by("-version").first()

    @property
    def approved_proof(self):
        return self.proofs.filter(decision=DesignProof.APPROVED).order_by("-version").first()

    def allowed_next(self):
        return [(s, dict(self.STATUSES)[s]) for s in self.TRANSITIONS.get(self.status, [])]

    @property
    def timeline(self):
        return self.events.order_by("created_at")


class NFCOrderItem(models.Model):
    """Which digital card each printed card opens (NFL-01, NFL-04, NFL-05)."""

    order = models.ForeignKey(NFCOrder, on_delete=models.CASCADE, related_name="items")
    card = models.ForeignKey("cards.Card", on_delete=models.PROTECT, related_name="nfc_items")
    quantity = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.quantity} × {self.card.slug}"

    @property
    def encode_url(self):
        """What is written to each tag before it is locked (NFC-05)."""
        return self.card.public_url("nfc")


class DesignProof(models.Model):
    PENDING = "pending"
    APPROVED = "approved"
    CHANGES = "changes_requested"

    order = models.ForeignKey(NFCOrder, on_delete=models.CASCADE, related_name="proofs")
    version = models.PositiveSmallIntegerField()
    file = models.FileField(upload_to=keep_name)
    staff_note = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decision = models.CharField(
        max_length=20,
        choices=[(PENDING, "Waiting for customer"), (APPROVED, "Approved"), (CHANGES, "Changes requested")],
        default=PENDING,
    )
    customer_comment = models.TextField(blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]
        unique_together = [("order", "version")]

    @property
    def is_pdf(self):
        return self.file.name.lower().endswith(".pdf")


class OrderEvent(models.Model):
    """The order's timeline: every status change and note."""

    order = models.ForeignKey(NFCOrder, on_delete=models.CASCADE, related_name="events")
    status = models.CharField(max_length=24, choices=NFCOrder.STATUSES, blank=True)
    message = models.TextField(blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    visible_to_customer = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]

    @property
    def status_label(self):
        return dict(NFCOrder.STATUSES).get(self.status, "")
