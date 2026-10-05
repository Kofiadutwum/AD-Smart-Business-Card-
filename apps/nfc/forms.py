from django import forms
from django.core.exceptions import ValidationError
from django_countries import countries

from apps.billing.models import Plan
from apps.billing.fx import latest_rate
from apps.core.currency import format_money_local
from apps.core.utils import normalise_phone, validate_design_file

from .models import DeliveryZone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _


class ZoneChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, zone):
        fee = format_money_local(zone.fee_minor, latest_rate()) if zone.fee_minor else gettext("Free")
        label = gettext("shipping and delivery") if zone.is_international else ""
        return f"{gettext(zone.name)} — {fee}{' ' + label if label else ''} · {gettext(zone.eta)}"


class NFCOrderForm(forms.Form):
    """Section 8 steps 1-7 on one page, in order."""

    full_name = forms.CharField(label=_("Name on the card"), max_length=120)
    business_name = forms.CharField(label=_("Business name"), max_length=160, required=False)
    position = forms.CharField(label=_("Position / job title"), max_length=120, required=False)
    email = forms.EmailField(label=_("Email on the card"))
    phone = forms.CharField(label=_("Phone on the card"), max_length=32)
    website = forms.CharField(label=_("Website"), max_length=255, required=False)
    logo = forms.FileField(
        label=_("Logo"), required=False,
        help_text=_("JPG, PNG, SVG or PDF up to 10 MB. We print from this file, so send the best quality you have."),
    )
    reference_design = forms.FileField(
        label=_("Reference design (optional)"), required=False,
        help_text=_("A card or style you like. JPG, PNG, SVG or PDF up to 10 MB."),
    )
    design_description = forms.CharField(
        label=_("Describe the design you want"),
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_("Colours, layout, what goes on the front and back."),
    )
    notes = forms.CharField(label=_("Anything else?"), widget=forms.Textarea(attrs={"rows": 2}), required=False)

    delivery_zone = ZoneChoiceField(
        label=_("Choose how you get your cards"), queryset=DeliveryZone.objects.none(), widget=forms.RadioSelect, empty_label=None
    )
    recipient_name = forms.CharField(label=_("Recipient’s name"), max_length=120, required=False)
    recipient_phone = forms.CharField(
        label=_("Recipient’s phone"), max_length=32, required=False,
        help_text=_("Outside Ghana, include the country code, for example +44 7700 900123."),
    )
    delivery_country = forms.ChoiceField(label=_("Country"), required=False, choices=[])
    delivery_address = forms.CharField(label=_("Street address"), max_length=255, required=False)
    delivery_city = forms.CharField(label=_("Town or city"), max_length=120, required=False)
    delivery_postcode = forms.CharField(label=_("Postcode / ZIP (if your country uses one)"), max_length=20, required=False)
    delivery_gps = forms.CharField(
        label=_("Ghana Post GPS (optional)"), max_length=16, required=False,
        widget=forms.TextInput(attrs={"placeholder": "GA-123-4567"}),
    )
    landmark = forms.CharField(label=_("Landmark or directions"), max_length=200, required=False)

    plan = forms.ModelChoiceField(
        label=_("Digital card plan"), queryset=Plan.objects.none(), widget=forms.RadioSelect, required=False, empty_label=None,
        help_text=_("NFC cards open your digital card, which needs an active plan. Buy both together here."),
    )
    promo = forms.CharField(label=_("Promo code"), max_length=32, required=False)
    accept_policy = forms.BooleanField(
        label=_("I have read and accept the Refund Policy and Terms of Service"),
        error_messages={"required": _("Please accept the Refund Policy before paying.")},
    )

    def __init__(self, *args, cards, needs_plan, plans, **kwargs):
        super().__init__(*args, **kwargs)
        self.cards = list(cards)
        self.needs_plan = needs_plan
        self.fields["delivery_zone"].queryset = DeliveryZone.objects.filter(is_active=True)
        self.fields["delivery_country"].choices = [("", _("Choose a country"))] + [
            (code, name) for code, name in countries if code != "GH"
        ]
        self.fields["plan"].queryset = plans
        if needs_plan:
            self.fields["plan"].required = True
        self.quantity_fields = []
        for card in self.cards:
            name = f"qty_{card.pk}"
            self.fields[name] = forms.IntegerField(
                label=_("Cards for %(name)s (/c/%(slug)s)") % {"name": card.full_name, "slug": card.slug},
                min_value=0, max_value=500, initial=1 if card == self.cards[0] else 0,
                widget=forms.NumberInput(attrs={"inputmode": "numeric", "class": "qty-input"}),
            )
            self.quantity_fields.append((card, name))

    def clean_phone(self):
        return normalise_phone(self.cleaned_data["phone"])

    def clean_logo(self):
        upload = self.cleaned_data.get("logo")
        return validate_design_file(upload, folder="nfc/logos") if upload else None

    def clean_reference_design(self):
        upload = self.cleaned_data.get("reference_design")
        return validate_design_file(upload, folder="nfc/references") if upload else None

    def clean_delivery_gps(self):
        value = self.cleaned_data.get("delivery_gps", "").strip().upper()
        return value

    def clean(self):
        cleaned = super().clean()
        items = [(card, cleaned.get(name) or 0) for card, name in self.quantity_fields]
        items = [(card, qty) for card, qty in items if qty > 0]
        if not items:
            raise ValidationError(_("Choose how many NFC cards you would like."))
        cleaned["items"] = items
        cleaned["quantity"] = sum(qty for _, qty in items)
        zone = cleaned.get("delivery_zone")
        if zone and not zone.is_pickup:
            needed = ["recipient_name", "recipient_phone", "delivery_address"]
            if zone.is_international:
                needed += ["delivery_country", "delivery_city"]
            for field in needed:
                if not cleaned.get(field):
                    self.add_error(field, _("Needed for delivery."))
            if cleaned.get("recipient_phone"):
                try:
                    cleaned["recipient_phone"] = normalise_phone(cleaned["recipient_phone"])
                except ValidationError as exc:
                    self.add_error("recipient_phone", exc)
        if zone and zone.is_international:
            cleaned["delivery_gps"] = ""
        else:
            # In Ghana (or pickup) the parcel stays in Ghana.
            cleaned["delivery_country"] = "GH"
            cleaned["delivery_postcode"] = ""
        return cleaned


class ChangesForm(forms.Form):
    comment = forms.CharField(
        label=_("What should we change?"), widget=forms.Textarea(attrs={"rows": 4}), max_length=2000
    )


class CancelForm(forms.Form):
    reason = forms.CharField(label=_("Why are you cancelling? (optional)"), required=False, widget=forms.Textarea(attrs={"rows": 3}))
