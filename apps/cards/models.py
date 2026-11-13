"""Digital cards and everything a card collects.

The permanent URL is ``/c/<slug>``. QR codes and NFC tags store only that URL,
so the owner can change every detail without reprinting anything (3.2).
"""

import uuid

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.core.utils import keep_name
from django.utils.translation import gettext_lazy as _

ACCENT_PRESETS = {
    "blue": "#0048A9",
    "navy": "#0A0E9C",
    "sky": "#0270B4",
    "purple": "#8805A9",
    "amber": "#B7790A",
    "ink": "#1F2430",
}
ACCENT_NAMES = {
    "blue": _("Blue"), "navy": _("Navy"), "sky": _("Sky"), "purple": _("Purple"), "amber": _("Amber"), "ink": _("Ink"),
}


def accent_choices():
    """The preset colours as (hex, name) pairs, named in the visitor's language."""
    return [(value, ACCENT_NAMES[name]) for name, value in ACCENT_PRESETS.items()]

TEMPLATES = [
    ("classic", _("Classic")),
    ("banner", _("Banner")),
    ("minimal", _("Minimal")),
]

BUTTON_STYLES = [("pill", _("Pill")), ("rounded", _("Rounded")), ("square", _("Square"))]
ICON_LAYOUTS = [("list", _("List")), ("grid", _("Grid")), ("row", _("Icon row"))]

# Fields a subscriber may hide from the public card (PRV-01).
HIDEABLE_FIELDS = [
    ("email", _("Email address")),
    ("phones", _("Phone numbers")),
    ("whatsapp", _("WhatsApp")),
    ("website", _("Website")),
    ("address", _("Physical address")),
    ("ghana_post_gps", _("Ghana Post GPS")),
    ("map", _("Map location")),
    ("bio", _("Short bio")),
]


class Card(models.Model):
    # Empty only for a pre-made card nobody has claimed yet (see PremadeCard).
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cards", null=True, blank=True
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)  # URL-07
    slug = models.CharField(max_length=64, unique=True)

    full_name = models.CharField(max_length=120)
    business_name = models.CharField(max_length=160, blank=True)
    job_title = models.CharField(max_length=120, blank=True)
    bio = models.TextField(max_length=300, blank=True)
    avatar = models.ImageField(upload_to=keep_name, blank=True)
    logo = models.ImageField(upload_to=keep_name, blank=True)

    whatsapp = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(max_length=255, blank=True)
    address = models.CharField(max_length=200, blank=True)
    ghana_post_gps = models.CharField(max_length=16, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    hidden_fields = models.JSONField(default=list, blank=True)

    template = models.CharField(max_length=16, choices=TEMPLATES, default="classic")
    accent = models.CharField(max_length=7, default=ACCENT_PRESETS["blue"])
    card_colour = models.CharField(max_length=7, blank=True)
    background = models.ImageField(upload_to=keep_name, blank=True)
    button_style = models.CharField(max_length=8, choices=BUTTON_STYLES, default="pill")
    icon_layout = models.CharField(max_length=8, choices=ICON_LAYOUTS, default="list")
    hide_branding = models.BooleanField(default=False)
    allow_indexing = models.BooleanField(default=False)  # SHR-12
    collect_leads = models.BooleanField(default=True)
    # The business's own card, shown in the landing page's phones. At most one; owned by the main admin.
    is_company = models.BooleanField(default=False)

    # Owner's own switch. A hidden card shows the inactive page.
    is_published = models.BooleanField(default=True)
    # Chosen by the owner when a downgrade leaves more cards than the plan allows.
    is_enabled = models.BooleanField(default=True)
    activated_at = models.DateTimeField(null=True, blank=True)  # null = Draft (ACT-01)

    # "Pay when you update" pre-made cards: no subscription. The card is live from set-up with the
    # features of ``update_plan``. Changes to details and links are free for the first year after the
    # card is made; after that they cost ``update_price_minor`` and unlock until ``updates_paid_until``.
    update_plan = models.ForeignKey("billing.Plan", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    update_price_minor = models.PositiveIntegerField(default=0)
    updates_paid_until = models.DateTimeField(null=True, blank=True)
    # The card the owner paid the Update Price with, kept (as Paystack's reusable token, never the number)
    # so the first Save after the year ends can be charged again once they confirm. Empty = pays by hand.
    update_authorization_code = models.CharField(max_length=64, blank=True)
    update_authorization_email = models.EmailField(blank=True)
    update_card_brand = models.CharField(max_length=24, blank=True)
    update_card_last4 = models.CharField(max_length=4, blank=True)
    update_card_expiry = models.CharField(max_length=7, blank=True)

    is_suspended = models.BooleanField(default=False)
    suspension_reason = models.TextField(blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    legacy_id = models.IntegerField(unique=True, null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.full_name} ({self.slug})"

    def get_absolute_url(self):
        return reverse("cards:public", args=[self.slug])

    def public_url(self, source=None):
        url = settings.SITE_URL + self.get_absolute_url()
        return f"{url}?src={source}" if source else url

    @property
    def initials(self):
        parts = [p for p in (self.full_name or "").split() if p]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()

    def shows(self, field):
        return field not in (self.hidden_fields or [])

    @property
    def has_nfc_orders(self):
        return self.nfc_items.exists()

    # --- public state (Section 6.1, Figure 5A) -------------------------------

    STATE_LIVE = "live"
    STATE_DRAFT = "draft"
    STATE_INACTIVE = "inactive"
    STATE_SUSPENDED = "suspended"
    STATE_DELETED = "deleted"
    STATE_UNCLAIMED = "unclaimed"

    @property
    def is_unclaimed(self):
        """A pre-made card that is waiting for its buyer to set it up."""
        return self.owner_id is None and self.deleted_at is None

    def public_state(self):
        if self.deleted_at:
            return self.STATE_DELETED
        if self.owner_id is None:
            return self.STATE_UNCLAIMED
        if self.is_suspended or self.owner.is_suspended:
            return self.STATE_SUSPENDED
        if self.activated_at is None:
            return self.STATE_DRAFT
        subscription = self.owner.subscription
        # An administrator's card needs no plan: it lives while they are active staff.
        # A pay-when-you-update card needs none either: it stays live on what it was last saved with.
        staff_card = self.owner.is_staff and self.owner.is_active
        if (
            (not staff_card and not self.is_update_card and (subscription is None or not subscription.is_live))
            or not self.is_published
            or not self.is_enabled
        ):
            return self.STATE_INACTIVE
        return self.STATE_LIVE

    @property
    def is_live(self):
        return self.public_state() == self.STATE_LIVE

    # --- pay when you update --------------------------------------------------

    @property
    def is_update_card(self):
        return self.update_plan_id is not None

    FREE_UPDATE_MONTHS = 12  # changes to details and links are free for this long after the card is made

    @property
    def updates_free_until(self):
        return (self.created_at or timezone.now()) + relativedelta(months=self.FREE_UPDATE_MONTHS)

    @property
    def updates_open_until(self):
        """The later of the free first year and what the owner paid for."""
        return max(filter(None, [self.updates_free_until, self.updates_paid_until]))

    @property
    def edit_locked(self):
        """True once the free year and any paid year are over: details, links and web address then need a payment.

        The business page is never locked."""
        return self.is_update_card and self.updates_open_until <= timezone.now()

    @property
    def pays_automatically(self):
        return bool(self.is_update_card and self.update_authorization_code)

    @property
    def update_card_expired(self):
        """A kept card past its expiry month cannot be charged; the owner is sent to pay another way."""
        try:
            month, year = (int(part) for part in self.update_card_expiry.split("/"))
        except ValueError:
            return False
        today = timezone.localdate()
        return (year, month) < (today.year, today.month)

    @property
    def update_card_label(self):
        """'Visa ending 4242', for showing the owner which card the next year is charged to."""
        if not self.update_card_last4:
            return ""
        brand = (self.update_card_brand or "").strip().title() or _("Card")
        return _("%(brand)s ending %(last4)s") % {"brand": brand, "last4": self.update_card_last4}

    # --- plan-aware rendering -------------------------------------------------

    @property
    def plan(self):
        subscription = self.owner.subscription
        return subscription.plan if subscription else None

    def effective(self):
        """What the public card may use under the owner's current plan.

        Enforced at render time as well as in the editor, so a downgrade can
        never leave a Basic card showing Professional features (AC-10).
        """
        from apps.billing.services import plan_for_card

        plan = plan_for_card(self)
        accent = self.accent
        if not plan.custom_colours and accent not in ACCENT_PRESETS.values():
            accent = ACCENT_PRESETS["blue"]
        return {
            "plan": plan,
            "accent": accent,
            "card_colour": self.card_colour if plan.custom_colours else "",
            "logo": self.logo if plan.allow_logo and self.logo else None,
            "background": self.background if plan.allow_advanced_style and self.background else None,
            "button_style": self.button_style if plan.allow_advanced_style else "pill",
            "icon_layout": self.icon_layout if plan.allow_advanced_style else "list",
            "template": self.template if plan.all_templates else "classic",
            "phones": list(self.phones.all()[: plan.max_phones]),
            "social_links": list(
                self.social_links.all()
                if plan.max_social_links is None
                else self.social_links.all()[: plan.max_social_links]
            ),
            "branding": "hidden"
            if plan.branding_footer == "removable" and self.hide_branding
            else plan.branding_footer,
        }


class CardSlug(models.Model):
    """Every slug ever used, so none can be handed to someone else (URL-05, URL-06).

    The current slug is also on Card.slug. Old slugs redirect to it; slugs of
    deleted cards stay here, retired, forever.
    """

    slug = models.CharField(max_length=64, unique=True)
    card = models.ForeignKey(Card, null=True, blank=True, on_delete=models.SET_NULL, related_name="slug_history")
    is_current = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.slug


class CardPhone(models.Model):
    card = models.ForeignKey(Card, on_delete=models.CASCADE, related_name="phones")
    number = models.CharField(max_length=20)
    label = models.CharField(max_length=32, blank=True, help_text=_("For example MTN, Telecel or Office."))
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.number


class SocialLink(models.Model):
    PLATFORMS = [
        ("linkedin", _("LinkedIn")),
        ("instagram", _("Instagram")),
        ("facebook", _("Facebook")),
        ("x", _("X")),
        ("tiktok", _("TikTok")),
        ("youtube", _("YouTube")),
        ("snapchat", _("Snapchat")),
        ("threads", _("Threads")),
        ("pinterest", _("Pinterest")),
        ("telegram", _("Telegram")),
        ("whatsapp", _("WhatsApp")),
        ("github", _("GitHub")),
        ("behance", _("Behance")),
        ("researchgate", _("ResearchGate")),
        ("orcid", _("ORCID")),
        ("scholar", _("Google Scholar")),
        ("website", _("Website")),
    ]
    ICONS = {"scholar": "googlescholar", "website": None}

    card = models.ForeignKey(Card, on_delete=models.CASCADE, related_name="social_links")
    platform = models.CharField(max_length=32, choices=PLATFORMS)
    url = models.URLField(max_length=500)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.get_platform_display()}: {self.url}"

    @property
    def label(self):
        return self.get_platform_display()

    @property
    def brand_icon(self):
        return self.ICONS.get(self.platform, self.platform)


class CardEvent(models.Model):
    """One row per view or click on a public card (Section 11)."""

    VIEW = "view"
    SAVE_CONTACT = "save_contact"
    CALL = "call"
    WHATSAPP = "whatsapp"
    EMAIL = "email"
    WEBSITE = "website"
    SOCIAL = "social"
    LOCATION = "location"
    KINDS = [
        (VIEW, _("View")),
        (SAVE_CONTACT, _("Contact saved")),
        (CALL, _("Phone click")),
        (WHATSAPP, _("WhatsApp click")),
        (EMAIL, _("Email click")),
        (WEBSITE, _("Website click")),
        (SOCIAL, _("Social click")),
        (LOCATION, _("Location click")),
    ]
    SOURCES = [("link", _("Direct link")), ("qr", _("QR code")), ("nfc", _("NFC tap"))]

    card = models.ForeignKey(Card, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=16, choices=KINDS, db_index=True)
    source = models.CharField(max_length=8, choices=SOURCES, default="link")
    detail = models.CharField(max_length=32, blank=True)
    visitor_hash = models.CharField(max_length=64, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["card", "occurred_at"])]


class Lead(models.Model):
    """Contact details a visitor sent back to the card owner."""

    card = models.ForeignKey(Card, on_delete=models.CASCADE, related_name="leads")
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    organisation = models.CharField(max_length=160, blank=True)
    note = models.TextField(blank=True)
    source = models.CharField(max_length=8, default="link")
    visitor_hash = models.CharField(max_length=64, blank=True, db_index=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} -> {self.card.slug}"


class CardReport(models.Model):
    """'Report this card' (MOD-01)."""

    REASONS = [
        ("impersonation", _("Impersonation")),
        ("fraud", _("Fraud or scam")),
        ("offensive", _("Offensive content")),
        ("other", _("Other")),
    ]
    OPEN = "open"
    STATUSES = [(OPEN, _("Open")), ("dismissed", _("Dismissed")), ("actioned", _("Actioned"))]

    card = models.ForeignKey(Card, on_delete=models.CASCADE, related_name="reports")
    reason = models.CharField(max_length=16, choices=REASONS)
    details = models.TextField(blank=True)
    reporter_email = models.EmailField(blank=True)
    visitor_hash = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=STATUSES, default=OPEN)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    handled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


# --------------------------------------------------------------------------
# Pre-made cards: printed with their QR code and NFC link, sold by shops
# --------------------------------------------------------------------------


class PremadeBatch(models.Model):
    """A run of pre-made cards printed together, sold ready to use.

    The price (GHS 450 by default) covers the printed card and the first year
    on ``plan``. The shop collects it; it is recorded as revenue from outside
    the website when the buyer sets the card up.

    A ``card_only`` batch (GHS 200 by default) sells the printed card alone:
    there is no ``plan``. The buyer sets the card up, then pays for a plan
    on the website like anyone else, which switches the card on.
    """

    name = models.CharField(max_length=120, help_text="The design, for example Gold Classic.")
    code = models.CharField(max_length=8, unique=True)
    quantity = models.PositiveIntegerField()
    price_minor = models.PositiveIntegerField(default=45000, help_text="What the buyer pays the shop, in pesewas.")
    plan = models.ForeignKey("billing.Plan", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    months = models.PositiveSmallIntegerField(default=12)
    card_only = models.BooleanField(default=False, help_text="The shop price covers the card only; no plan is included.")
    # No plan is paid for up front: the card is live at once, with the features of ``plan``, and the
    # buyer pays ``update_price_minor`` for a year of changes whenever they want to change something.
    pay_to_update = models.BooleanField(default=False)
    update_price_minor = models.PositiveIntegerField(default=10000, help_text="The Update Price, in pesewas.")
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.code} · {self.name}"


class PremadeCard(models.Model):
    """One printed card: an unclaimed digital card, its PIN and who claimed it."""

    card = models.OneToOneField(Card, on_delete=models.CASCADE, related_name="premade")
    batch = models.ForeignKey(PremadeBatch, on_delete=models.CASCADE, related_name="cards")
    serial = models.PositiveIntegerField()
    # Keyed hash of the PIN printed on the back; the PIN itself is never stored.
    pin_hash = models.CharField(max_length=64)
    pin_failures = models.PositiveSmallIntegerField(default=0)
    pin_locked_until = models.DateTimeField(null=True, blank=True)

    claimed_at = models.DateTimeField(null=True, blank=True)
    claimed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="claimed_premade_cards"
    )
    payment = models.ForeignKey(
        "billing.Payment", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    voided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["batch_id", "serial"]
        constraints = [models.UniqueConstraint(fields=["batch", "serial"], name="premade_serial_per_batch")]

    def __str__(self):
        return self.label

    @property
    def label(self):
        return f"{self.batch.code}-{self.serial:04d}"

    STATUS_UNCLAIMED, STATUS_CLAIMED, STATUS_VOID = "unclaimed", "claimed", "void"

    @property
    def status(self):
        if self.claimed_at:
            return self.STATUS_CLAIMED
        if self.voided_at:
            return self.STATUS_VOID
        return self.STATUS_UNCLAIMED


class CardUpdate(models.Model):
    """Editor changes held while the owner pays to unlock a pay-when-you-update card.

    At most one per card: a new Save replaces the old one. ``data`` is the form post (field name to
    list of values) and ``files`` the pictures chosen in the same Save, already in storage. Applied
    when the payment clears; discarded (and its pictures deleted) after 48 hours.
    """

    card = models.OneToOneField(Card, on_delete=models.CASCADE, related_name="held_update")
    data = models.JSONField(default=dict)
    files = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Held changes for {self.card.slug}"


# --------------------------------------------------------------------------
# Business page: a landing page for the owner's business, linked from the card
# --------------------------------------------------------------------------

PRODUCT_IMAGE_BYTES = 30 * 1024  # every product picture is squeezed down to this
MAX_PRODUCT_PICTURES = 5  # the cover (BusinessProduct.image) plus up to four more (ProductPhoto)
MAX_SERVICES = 12
MAX_REVIEWS = 20


class BusinessPage(models.Model):
    """One per account, so a team shares one page. Shown at /b/<slug> when switched on.

    The logo is not stored here: the page borrows the card's logo, or its photo.
    How many products show is the plan's ``max_products``, checked when it is shown.
    """

    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="business_page")
    is_on = models.BooleanField(default=False)
    tagline = models.CharField(max_length=120, blank=True)
    about = models.TextField(max_length=1500, blank=True)
    hours = models.TextField(max_length=300, blank=True)
    # The marketplace at /shops/: on by default, the owner can leave; staff can hide a shop that breaks the rules.
    in_marketplace = models.BooleanField(default=True)
    marketplace_hidden = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Business page of {self.owner}"

    def primary_card(self):
        """The card whose contact details, colours and link the page uses."""
        cards = self.owner.cards.filter(deleted_at__isnull=True)
        # The main admin also has a personal card; the business page belongs to the company card.
        return cards.filter(is_company=True).first() or cards.filter(is_enabled=True).first() or cards.first()

    def get_absolute_url(self):
        card = self.primary_card()
        return reverse("cards:business", args=[card.slug]) if card else ""

    def public_url(self):
        return settings.SITE_URL + self.get_absolute_url()


class ProductCategory(models.Model):
    """The marketplace's sidebar. Staff keep the list; each owner picks one per product."""

    name = models.CharField(max_length=60)
    slug = models.SlugField(max_length=70, unique=True)
    position = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "name"]
        verbose_name_plural = "product categories"

    def __str__(self):
        return self.name


class BusinessProduct(models.Model):
    page = models.ForeignKey(BusinessPage, on_delete=models.CASCADE, related_name="products")
    image = models.ImageField(upload_to=keep_name)
    title = models.CharField(max_length=120)
    description = models.TextField(max_length=500, blank=True)
    price = models.CharField(max_length=40, blank=True)  # free text, so any currency or "from" works
    category = models.ForeignKey(ProductCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name="products")
    is_hidden = models.BooleanField(default=False)  # hidden by staff: gone from the shop page and the marketplace
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.title


class ProductPhoto(models.Model):
    """The pictures of a product after its cover. Shoppers see them with the arrows in the big view."""

    product = models.ForeignKey(BusinessProduct, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to=keep_name)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]


class BusinessEvent(models.Model):
    """A product opened, a WhatsApp buy tapped or an enquiry sent. Ranks shops and the landing slider.

    No personal data: ``visitor_hash`` is the same salted digest the card analytics use. One row per
    visitor, kind and product a day, so refreshing the page does not push a shop up.
    """

    VIEW = "view"  # the shop page was opened
    PRODUCT = "product"  # a product was opened (from the slider, the marketplace or a tap on the shop page)
    BUY = "buy"  # the "Buy" button (opens WhatsApp) was tapped
    ENQUIRY = "enquiry"
    KINDS = [(VIEW, "Shop view"), (PRODUCT, "Product view"), (BUY, "Buy tap"), (ENQUIRY, "Enquiry")]

    page = models.ForeignKey(BusinessPage, on_delete=models.CASCADE, related_name="events")
    product = models.ForeignKey(BusinessProduct, null=True, blank=True, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=8, choices=KINDS)
    visitor_hash = models.CharField(max_length=64, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["page", "kind", "occurred_at"]),
            models.Index(fields=["product", "kind", "occurred_at"]),
        ]


class BusinessService(models.Model):
    page = models.ForeignKey(BusinessPage, on_delete=models.CASCADE, related_name="services")
    title = models.CharField(max_length=120)
    description = models.TextField(max_length=300, blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.title


class BusinessReview(models.Model):
    """A customer's words, added by the owner."""

    page = models.ForeignKey(BusinessPage, on_delete=models.CASCADE, related_name="reviews")
    author = models.CharField(max_length=120)
    text = models.TextField(max_length=400)
    rating = models.PositiveSmallIntegerField(null=True, blank=True, choices=[(n, str(n)) for n in range(1, 6)])
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.author

    @property
    def stars(self):
        return "★" * (self.rating or 0) + "☆" * (5 - (self.rating or 0))
