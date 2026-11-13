from django import forms
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.billing.models import Plan, PromoCode, SeatBand
from apps.core.fields import PictureField
from apps.core.models import SiteSettings
from apps.core.utils import ghs_to_minor, process_image, validate_design_file
from apps.marketing.models import GalleryImage
from apps.nfc.models import DeliveryZone, NFCOrder, PriceTier

BASIC_TERM_MONTHS = 36  # the 3-year Basic shop card


class CedisField(forms.DecimalField):
    """Staff type cedis; the database stores pesewas."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_digits", 10)
        kwargs.setdefault("decimal_places", 2)
        kwargs.setdefault("min_value", 0)
        super().__init__(*args, **kwargs)


class StaffLoginForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "username"}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))


class StaffCodeForm(forms.Form):
    code = forms.RegexField(
        label="6-digit code from your email", regex=r"^\s*\d{6}\s*$",
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "maxlength": 6}),
        error_messages={"invalid": "Enter the 6 digits from the email."},
    )


class ReasonForm(forms.Form):
    reason = forms.CharField(label="Reason (recorded in the audit log)", widget=forms.Textarea(attrs={"rows": 3}))


class CustomerPasswordResetForm(ReasonForm):
    """Support resets a customer's password by hand: three checks must be ticked, and a reason is kept."""

    checked_name = forms.BooleanField(label="I checked the caller's full name matches the account")
    checked_phone = forms.BooleanField(label="I checked the caller's phone number matches the number on the account")
    checked_activity = forms.BooleanField(
        label="I checked one recent payment, receipt or card link only the owner would know"
    )


class CustomerEmailChangeForm(CustomerPasswordResetForm):
    """Support puts in a new email for a customer who cannot reach the old one: same three checks, plus a reason."""

    new_email = forms.EmailField(label="New email address")
    field_order = ["new_email", "checked_name", "checked_phone", "checked_activity", "reason"]


class ManualActivationForm(ReasonForm):
    plan = forms.ModelChoiceField(queryset=Plan.objects.all(), empty_label=None)
    months = forms.IntegerField(min_value=1, max_value=36, initial=12)
    seats = forms.IntegerField(
        label="Team size", required=False, min_value=1, max_value=1000,
        help_text="Business plan only: how many people. Leave empty for the people included in the price.",
    )
    amount = CedisField(label="Amount received (GHS)", initial=0, help_text="0 for complimentary cards.")


class RefundForm(ReasonForm):
    amount = CedisField(label="Refund amount (GHS)")


class PromoCodeForm(forms.ModelForm):
    value_input = forms.DecimalField(
        label="Value", min_value=0, decimal_places=2,
        help_text="A percentage (e.g. 20) or an amount in GHS (e.g. 50.00).",
    )

    class Meta:
        model = PromoCode
        fields = ["code", "description", "kind", "applies_to", "max_uses", "starts_at", "expires_at", "is_active"]
        widgets = {
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "expires_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["value_input"].initial = (
                self.instance.value if self.instance.kind == PromoCode.KIND_PERCENT else self.instance.value / 100
            )

    def clean(self):
        cleaned = super().clean()
        value = cleaned.get("value_input")
        if value is not None:
            if cleaned.get("kind") == PromoCode.KIND_PERCENT:
                if not 0 < value <= 100:
                    self.add_error("value_input", "A percentage must be between 1 and 100.")
                cleaned["value"] = int(value)
            else:
                cleaned["value"] = ghs_to_minor(value)
        return cleaned

    def save(self, commit=True):
        self.instance.value = self.cleaned_data["value"]
        return super().save(commit)


class ProofForm(forms.Form):
    file = forms.FileField(label="Design proof", help_text="JPG, PNG, WebP, SVG or PDF up to 10 MB.")
    note = forms.CharField(label="Note to the customer", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def clean_file(self):
        return validate_design_file(self.cleaned_data["file"], folder="nfc/proofs")


class StatusForm(forms.Form):
    status = forms.ChoiceField(choices=NFCOrder.STATUSES)
    note = forms.CharField(label="Message to the customer (optional)", required=False, widget=forms.Textarea(attrs={"rows": 2}))


class QualityForm(forms.ModelForm):
    class Meta:
        model = NFCOrder
        fields = ["qc_android_ok", "qc_iphone_ok", "tags_locked", "courier_name", "tracking_number"]
        labels = {
            "qc_android_ok": "Every card tap-tested on an Android phone",
            "qc_iphone_ok": "Every card tap-tested on an iPhone",
            "tags_locked": "Every tag written with the ?src=nfc link and locked",
            "courier_name": "Courier",
            "tracking_number": "Tracking / waybill number",
        }


class NoteForm(forms.Form):
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))
    visible_to_customer = forms.BooleanField(required=False, label="Show this note to the customer")


class GalleryForm(forms.ModelForm):
    upload = PictureField(label="Picture", required=False, help_text="JPG, PNG, HEIC, WebP and more, up to 10 MB. Landscape pictures fill the slides best.")

    class Meta:
        model = GalleryImage
        fields = ["title", "description", "alt_text", "caption", "action_label", "action_url", "focus", "display_order", "is_published"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}

    def clean(self):
        cleaned = super().clean()
        upload = cleaned.get("upload")
        if upload:
            try:
                cleaned["processed"] = process_image(upload, folder="gallery", max_px=1600)
            except ValidationError as exc:
                self.add_error("upload", exc)
        elif not self.instance.pk:
            self.add_error("upload", "Choose a picture.")
        return cleaned


class SiteSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        exclude = ["updated_at"]


class PlanForm(forms.ModelForm):
    price = CedisField(label="Annual price (GHS)")

    class Meta:
        model = Plan
        exclude = ["price_minor", "code"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["price"].initial = self.instance.price_minor / 100 if self.instance.pk else None

    def save(self, commit=True):
        self.instance.price_minor = ghs_to_minor(self.cleaned_data["price"])
        return super().save(commit)


class SeatBandForm(forms.ModelForm):
    unit_price = CedisField(label="Price per person (GHS a year)")

    class Meta:
        model = SeatBand
        fields = ["min_seats", "max_seats"]
        labels = {"min_seats": "From person", "max_seats": "To person"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["max_seats"].help_text = "Leave empty on the last band for no limit."
        if self.instance.pk:
            self.fields["unit_price"].initial = self.instance.unit_price_minor / 100

    def save(self, commit=True):
        self.instance.unit_price_minor = ghs_to_minor(self.cleaned_data["unit_price"])
        return super().save(commit)


def seat_band_problems(included, bands):
    """Bands must run on from the people in the base price with no gaps or overlaps,
    and only the last may be open-ended. ``bands`` are (min, max) pairs."""
    bands = sorted(bands)
    if not bands:
        return None
    expected = included + 1
    for i, (low, high) in enumerate(bands):
        if low != expected:
            return f"the band starting at person {low} should start at person {expected}"
        if high is None:
            if i != len(bands) - 1:
                return "only the last band can be left without an upper limit"
            return None
        if high < low:
            return f"the band starting at person {low} ends before it starts"
        expected = high + 1
    return None


class TierForm(forms.ModelForm):
    unit_price = CedisField(label="Price per card (GHS)")

    class Meta:
        model = PriceTier
        fields = ["min_quantity", "max_quantity", "mode"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["unit_price"].initial = self.instance.unit_price_minor / 100

    def save(self, commit=True):
        self.instance.unit_price_minor = ghs_to_minor(self.cleaned_data["unit_price"])
        return super().save(commit)


class ZoneForm(forms.ModelForm):
    fee = CedisField(label="Fee (GHS)")

    class Meta:
        model = DeliveryZone
        fields = ["name", "description", "eta", "is_pickup", "is_international", "is_active", "sort_order"]
        labels = {
            "eta": "Estimated time",
            "is_pickup": "Pickup from the office (no address needed)",
            "is_international": "Outside Ghana (international shipping)",
            "is_active": "Offer this option",
            "sort_order": "Order in the list",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["fee"].initial = self.instance.fee_minor / 100

    def save(self, commit=True):
        self.instance.fee_minor = ghs_to_minor(self.cleaned_data["fee"])
        return super().save(commit)


class AdminCreateForm(forms.Form):
    full_name = forms.CharField(max_length=120)
    email = forms.EmailField()
    role = forms.ChoiceField(choices=User.STAFF_ROLES)

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("That email already has an account.")
        return email


class RoleForm(forms.Form):
    role = forms.ChoiceField(choices=User.STAFF_ROLES)


class BulkEmailForm(forms.Form):
    AUDIENCES = [
        ("one", "One subscriber"),
        ("active", "All active subscribers"),
        ("expiring", "Subscribers expiring within 30 days"),
        ("inactive", "Subscribers whose plan has ended"),
        ("all", "Every customer account"),
    ]
    audience = forms.ChoiceField(choices=AUDIENCES)
    email = forms.EmailField(required=False, help_text="Only for 'One subscriber'.")
    subject = forms.CharField(max_length=160)
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 8}))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("audience") == "one" and not cleaned.get("email"):
            self.add_error("email", "Enter the subscriber's email.")
        return cleaned


class PremadeBatchForm(forms.Form):
    """Make a batch of pre-made cards for shops to sell (staff only)."""

    name = forms.CharField(label="Design name", max_length=120, help_text="For example Gold Classic. Only staff see it.")
    quantity = forms.IntegerField(label="How many cards", min_value=1, max_value=500, initial=100, help_text="Up to 500 at a time.")
    term = forms.ChoiceField(
        label="What the card includes",
        choices=[
            ("year", "A year of the plan chosen below"),
            ("basic3", "Basic for 3 years, then it renews as normal"),
            ("cardonly", "The card only: the buyer pays for a plan online to switch it on"),
            ("payupdate", "Pay when updating: live at once, the buyer pays only to change their details (Basic or Professional)"),
        ],
        initial="year",
        help_text="The 3-year Basic card has only the Basic features, and the card-only set has no plan inside it; the plan below is ignored for both. For pay-when-updating, the plan below sets the features.",
    )
    plan = forms.ModelChoiceField(
        label="Plan included for the first year", queryset=Plan.objects.none(), empty_label=None, required=False
    )
    price = CedisField(
        label="Price in the shop (GHS)", required=False,
        help_text="What the buyer pays the shop. Leave empty for the usual price: 450 with a plan inside, 200 for the card only or pay-when-updating. It is counted as revenue from outside the website when the card is set up.",
    )
    update_price = CedisField(
        label="Update Price (GHS)", required=False,
        help_text="Pay-when-updating cards only: what the buyer pays online to change their card. It opens changes for 12 months. Leave empty for 100.",
    )
    note = forms.CharField(label="Note (optional)", max_length=200, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        plans = Plan.objects.filter(is_active=True)
        self.fields["plan"].queryset = plans
        professional = plans.filter(code="professional").first()
        if professional:
            self.fields["plan"].initial = professional.pk

    def clean(self):
        cleaned = super().clean()
        term = cleaned.get("term")
        if cleaned.get("price") is None:
            cleaned["price"] = 200 if term in ("cardonly", "payupdate") else 450
        if cleaned.get("update_price") is None:
            cleaned["update_price"] = 100
        if term == "payupdate":
            from apps.cards.premade import PAY_TO_UPDATE_PLANS

            plan = cleaned.get("plan")
            if not plan or plan.code not in PAY_TO_UPDATE_PLANS:
                self.add_error("plan", "Choose the Basic or Professional plan for a pay-when-updating card.")
            cleaned["months"] = 0
        elif term == "cardonly":
            cleaned["plan"], cleaned["months"] = None, 0
        elif term == "basic3":
            cleaned["plan"] = self.fields["plan"].queryset.filter(code="basic").first()
            cleaned["months"] = BASIC_TERM_MONTHS
            if not cleaned["plan"]:
                self.add_error("term", "The Basic plan is switched off. Switch it on under Plans first.")
        elif term == "year":
            if not cleaned.get("plan"):
                self.add_error("plan", "Choose a plan.")
            else:
                cleaned["months"] = cleaned["plan"].duration_months
        return cleaned
