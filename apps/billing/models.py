"""Plans, subscriptions, payments, refunds, promo codes and exchange rates.

Money is always an integer number of pesewas. GHS is the currency charged;
USD is display-only (Section 5.1).
"""

from datetime import timedelta

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import SiteSettings


class Plan(models.Model):
    """A yearly subscription plan with its feature allowances (Section 5.2)."""

    code = models.SlugField(max_length=32, unique=True)
    name = models.CharField(max_length=64)
    blurb = models.CharField(max_length=160, blank=True)
    price_minor = models.PositiveIntegerField(help_text="Annual price in pesewas.")
    duration_months = models.PositiveSmallIntegerField(default=12)

    max_cards = models.PositiveSmallIntegerField(default=1)
    max_phones = models.PositiveSmallIntegerField(default=2)
    max_social_links = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Leave empty for unlimited."
    )
    all_templates = models.BooleanField(default=False)
    custom_colours = models.BooleanField(default=False)
    shared_brand_theme = models.BooleanField(default=False)
    allow_logo = models.BooleanField(default=False)
    allow_advanced_style = models.BooleanField(
        default=False, help_text="Background image, button style and icon arrangement."
    )
    branding_footer = models.CharField(
        max_length=16,
        choices=[("shown", "Shown"), ("small", "Shown (small)"), ("removable", "Removable")],
        default="shown",
    )
    full_analytics = models.BooleanField(default=False)
    priority_support = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "price_minor"]

    def __str__(self):
        return self.name

    @property
    def social_limit_label(self):
        return "Unlimited" if self.max_social_links is None else f"Up to {self.max_social_links}"

    def feature_rows(self):
        """Human-readable feature list for pricing pages, in matrix order."""
        cards = "1 digital card" if self.max_cards == 1 else f"Up to {self.max_cards} digital cards"
        rows = [
            (True, cards),
            (True, "Permanent link, QR code and .vcf contact"),
            (True, f"Up to {self.max_phones} phone numbers per card"),
            (True, f"{self.social_limit_label} social media links"),
            (True, "All templates" if self.all_templates else "1 standard template"),
            (
                True,
                "Any colour + shared brand theme"
                if self.shared_brand_theme
                else ("Any card colour" if self.custom_colours else "Preset colours"),
            ),
            (self.allow_logo, "Business logo on card"),
            (self.allow_advanced_style, "Background image, button style, icon layout"),
            (self.branding_footer == "removable", "Remove “Powered by AD Smart”"),
            (True, "Full analytics" if self.full_analytics else "Total views"),
            (self.priority_support, "Priority support (4 working hours)"),
        ]
        return rows


class Subscription(models.Model):
    """One subscription per account. It carries the plan and the paid-up date.

    Renewing moves ``expires_at`` forward from the current expiry, never from
    today, so early renewal loses no paid time (PLN-03). The card URL, QR code
    and NFC destination never change, whatever happens here (EXP-07).
    """

    STATE_ACTIVE = "active"
    STATE_EXPIRING = "expiring_soon"
    STATE_GRACE = "grace"
    STATE_INACTIVE = "inactive"
    STATE_LABELS = {
        STATE_ACTIVE: "Active",
        STATE_EXPIRING: "Expiring soon",
        STATE_GRACE: "Grace period",
        STATE_INACTIVE: "Inactive",
    }

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscription_record"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    started_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    # A downgrade waits for the next renewal (PLN-02).
    pending_plan = models.ForeignKey(
        Plan, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    pending_plan_from = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user} · {self.plan} until {self.expires_at:%d %b %Y}"

    # --- state ---------------------------------------------------------------

    @property
    def grace_ends_at(self):
        return self.expires_at + timedelta(days=SiteSettings.load().grace_period_days)

    def state_at(self, when=None):
        when = when or timezone.now()
        if self.cancelled_at:
            return self.STATE_INACTIVE
        if when <= self.expires_at:
            if self.expires_at - when <= timedelta(days=30):
                return self.STATE_EXPIRING
            return self.STATE_ACTIVE
        if when <= self.grace_ends_at:
            return self.STATE_GRACE
        return self.STATE_INACTIVE

    @property
    def state(self):
        return self.state_at()

    @property
    def state_label(self):
        return self.STATE_LABELS[self.state]

    @property
    def is_live(self):
        """Cards keep working while Active, Expiring Soon and in Grace (Section 6.1)."""
        return self.state != self.STATE_INACTIVE

    @property
    def days_left(self):
        return (self.expires_at - timezone.now()).days

    @property
    def full_months_left(self):
        now = timezone.now()
        if now >= self.expires_at:
            return 0
        delta = relativedelta(self.expires_at, now)
        return delta.years * 12 + delta.months

    # --- changes -------------------------------------------------------------

    def extend(self, months):
        """Add ``months`` from the later of now and the current expiry."""
        now = timezone.now()
        base = self.expires_at if self.expires_at and self.expires_at > now and not self.cancelled_at else now
        if base == now:
            self.started_at = now
        self.expires_at = base + relativedelta(months=months)
        self.cancelled_at = None

    def apply_pending_plan(self):
        if self.pending_plan_id and self.pending_plan_from and timezone.now() >= self.pending_plan_from:
            self.plan_id = self.pending_plan_id
            self.pending_plan = None
            self.pending_plan_from = None
            return True
        return False


class PromoCode(models.Model):
    """Discount codes (ADM-05)."""

    KIND_PERCENT = "percent"
    KIND_FIXED = "fixed"
    SCOPE_SUBSCRIPTION = "subscription"
    SCOPE_NFC = "nfc"
    SCOPE_BOTH = "both"

    code = models.CharField(max_length=32, unique=True)
    description = models.CharField(max_length=160, blank=True)
    kind = models.CharField(
        max_length=8, choices=[(KIND_PERCENT, "Percentage"), (KIND_FIXED, "Fixed amount")]
    )
    value = models.PositiveIntegerField(help_text="Percent (1-100) or amount in pesewas.")
    applies_to = models.CharField(
        max_length=16,
        choices=[
            (SCOPE_SUBSCRIPTION, "Subscriptions"),
            (SCOPE_NFC, "NFC orders"),
            (SCOPE_BOTH, "Both"),
        ],
        default=SCOPE_BOTH,
    )
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    used_count = models.PositiveIntegerField(default=0)
    starts_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    @property
    def label(self):
        if self.kind == self.KIND_PERCENT:
            return f"{self.value}% off"
        from apps.core.utils import format_ghs

        return f"{format_ghs(self.value)} off"

    def problem_for(self, scope):
        """Why this code cannot be used now, or None when it can."""
        now = timezone.now()
        if not self.is_active:
            return "That promo code is no longer active."
        if self.starts_at and now < self.starts_at:
            return "That promo code is not active yet."
        if self.expires_at and now > self.expires_at:
            return "That promo code has expired."
        if self.max_uses is not None and self.used_count >= self.max_uses:
            return "That promo code has been fully used."
        if self.applies_to not in (self.SCOPE_BOTH, scope):
            return "That promo code does not apply to this purchase."
        return None

    @property
    def is_live(self):
        return self.problem_for(self.applies_to) is None

    def discount_on(self, amount_minor):
        if self.kind == self.KIND_PERCENT:
            from apps.core.utils import percent_of

            return min(amount_minor, percent_of(amount_minor, min(self.value, 100)))
        return min(amount_minor, self.value)


class Payment(models.Model):
    """Every payment attempt, successful or not (PAY-07)."""

    PURPOSE_SUBSCRIPTION = "subscription"
    PURPOSE_UPGRADE = "upgrade"
    PURPOSE_NFC = "nfc_order"
    PURPOSES = [
        (PURPOSE_SUBSCRIPTION, "Subscription"),
        (PURPOSE_UPGRADE, "Plan upgrade"),
        (PURPOSE_NFC, "NFC order"),
    ]

    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    ABANDONED = "abandoned"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"
    STATUSES = [
        (PENDING, "Pending"),
        (SUCCESS, "Successful"),
        (FAILED, "Failed"),
        (ABANDONED, "Abandoned"),
        (REFUNDED, "Refunded"),
        (PARTIALLY_REFUNDED, "Partially refunded"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments"
    )
    reference = models.CharField(max_length=64, unique=True)
    purpose = models.CharField(max_length=16, choices=PURPOSES)
    plan = models.ForeignKey(Plan, null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    months = models.PositiveSmallIntegerField(default=0)
    nfc_order = models.ForeignKey(
        "nfc.NFCOrder", null=True, blank=True, on_delete=models.PROTECT, related_name="payments"
    )

    subtotal_minor = models.PositiveIntegerField(default=0)
    discount_minor = models.PositiveIntegerField(default=0)
    tax_minor = models.PositiveIntegerField(default=0)
    amount_minor = models.PositiveIntegerField(help_text="What the customer is charged, in pesewas.")
    currency = models.CharField(max_length=3, default="GHS")
    promo_code = models.ForeignKey(PromoCode, null=True, blank=True, on_delete=models.SET_NULL)
    line_items = models.JSONField(default=list, blank=True)

    status = models.CharField(max_length=24, choices=STATUSES, default=PENDING, db_index=True)
    gateway = models.CharField(max_length=16, default="paystack")
    channel = models.CharField(max_length=32, blank=True)
    gateway_fee_minor = models.PositiveIntegerField(default=0)
    gateway_response = models.JSONField(default=dict, blank=True)
    flag = models.CharField(max_length=64, blank=True, help_text="Set when verification found a problem.")

    # The USD rate shown to the customer when they paid (CUR-05).
    fx_rate = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    fx_rate_at = models.DateTimeField(null=True, blank=True)

    receipt_number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    receipt_emailed_at = models.DateTimeField(null=True, blank=True)
    manual_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True, db_index=True)
    legacy_source = models.CharField(max_length=32, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference

    @property
    def refunded_minor(self):
        return sum(r.amount_minor for r in self.refunds.all() if r.status == Refund.DONE)

    @property
    def net_minor(self):
        return self.amount_minor - self.gateway_fee_minor - self.refunded_minor

    @property
    def description(self):
        if self.purpose == self.PURPOSE_NFC:
            parts = [f"NFC order {self.nfc_order.order_number}" if self.nfc_order else "NFC order"]
            if self.plan_id:
                parts.append(f"{self.plan.name} plan")
            return " + ".join(parts)
        if self.purpose == self.PURPOSE_UPGRADE:
            return f"Upgrade to {self.plan.name}" if self.plan else "Plan upgrade"
        return f"{self.plan.name} plan, {self.months} months" if self.plan else "Subscription"

    @property
    def is_manual(self):
        return self.gateway == "manual"


class Refund(models.Model):
    REQUESTED = "requested"
    DONE = "done"
    FAILED = "failed"

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    amount_minor = models.PositiveIntegerField()
    reason = models.TextField()
    status = models.CharField(
        max_length=16,
        choices=[(REQUESTED, "Requested"), (DONE, "Refunded"), (FAILED, "Failed")],
        default=REQUESTED,
    )
    gateway_reference = models.CharField(max_length=64, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class ExchangeRate(models.Model):
    """GHS per 1 USD, as fetched from the provider (CUR-02, CUR-03)."""

    ghs_per_usd = models.DecimalField(max_digits=12, decimal_places=6)
    provider = models.CharField(max_length=64)
    fetched_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-fetched_at"]
        get_latest_by = "fetched_at"

    def __str__(self):
        return f"1 USD = {self.ghs_per_usd} GHS ({self.provider}, {self.fetched_at:%Y-%m-%d %H:%M})"
