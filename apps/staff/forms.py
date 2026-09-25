from django import forms
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.billing.models import Plan, PromoCode
from apps.core.models import SiteSettings
from apps.core.utils import ghs_to_minor, process_image, validate_design_file
from apps.marketing.models import GalleryImage
from apps.nfc.models import DeliveryZone, NFCOrder, PriceTier


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


class TotpForm(forms.Form):
    code = forms.RegexField(
        label="6-digit code from your authenticator app", regex=r"^\s*\d{6}\s*$",
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "maxlength": 6}),
        error_messages={"invalid": "Enter the 6 digits shown in the app."},
    )


class ReasonForm(forms.Form):
    reason = forms.CharField(label="Reason (recorded in the audit log)", widget=forms.Textarea(attrs={"rows": 3}))


class ManualActivationForm(ReasonForm):
    plan = forms.ModelChoiceField(queryset=Plan.objects.all(), empty_label=None)
    months = forms.IntegerField(min_value=1, max_value=36, initial=12)
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
    upload = forms.ImageField(label="Picture", required=False, help_text="JPG, PNG or WebP up to 5 MB. Landscape pictures fill the slides best.")

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
