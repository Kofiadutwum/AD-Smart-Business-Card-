"""Card editor forms (Sections 3.1, 12, PRV-01)."""

import re

from django import forms
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory

from apps.cards.colors import valid_hex
from apps.cards.models import ACCENT_PRESETS, HIDEABLE_FIELDS, Card, CardPhone, SocialLink
from apps.core.utils import normalise_phone, process_image

GPS_RE = re.compile(r"^[A-Z]{2}-\d{1,4}-\d{3,4}$")


class CardForm(forms.ModelForm):
    avatar_upload = forms.ImageField(
        label="Profile photo", required=False, help_text="JPG, PNG or WebP up to 5 MB. We resize it for fast loading."
    )
    remove_avatar = forms.BooleanField(label="Remove photo", required=False)
    logo_upload = forms.ImageField(label="Business logo", required=False, help_text="PNG with a transparent background works best.")
    remove_logo = forms.BooleanField(label="Remove logo", required=False)
    background_upload = forms.ImageField(label="Background image", required=False)
    remove_background = forms.BooleanField(label="Remove background", required=False)
    accent_preset = forms.ChoiceField(
        label="Card colour", choices=[(v, k.title()) for k, v in ACCENT_PRESETS.items()] + [("custom", "Custom")],
        widget=forms.RadioSelect, required=False,
    )
    accent_custom = forms.CharField(label="Custom colour", required=False, widget=forms.TextInput(attrs={"type": "color"}))
    card_colour = forms.CharField(label="Band colour", required=False, widget=forms.TextInput(attrs={"type": "color"}))
    visible = forms.MultipleChoiceField(
        label="Show on my public card", choices=HIDEABLE_FIELDS, widget=forms.CheckboxSelectMultiple, required=False
    )

    class Meta:
        model = Card
        fields = [
            "full_name", "job_title", "business_name", "bio",
            "whatsapp", "email", "website", "address", "ghana_post_gps", "latitude", "longitude",
            "template", "button_style", "icon_layout", "hide_branding",
            "allow_indexing", "collect_leads", "is_published",
        ]
        labels = {
            "full_name": "Full name",
            "job_title": "Position / job title",
            "business_name": "Business name",
            "bio": "Short bio",
            "whatsapp": "WhatsApp number",
            "email": "Email address",
            "website": "Website",
            "address": "Physical address",
            "ghana_post_gps": "Ghana Post GPS address",
            "template": "Layout",
            "button_style": "Button style",
            "icon_layout": "Social links",
            "hide_branding": "Remove “Powered by AD Smart”",
            "allow_indexing": "Let search engines show my card",
            "collect_leads": "Let visitors send me their details",
            "is_published": "Card is visible to the public",
        }
        help_texts = {
            "bio": "Up to 300 characters.",
            "whatsapp": "Can differ from your call number.",
            "ghana_post_gps": "For example GA-123-4567.",
            "allow_indexing": "Off by default, so your card only reaches people you share it with.",
        }
        widgets = {
            "bio": forms.Textarea(attrs={"rows": 3, "maxlength": 300}),
            "template": forms.RadioSelect,
            "button_style": forms.RadioSelect,
            "icon_layout": forms.RadioSelect,
            "latitude": forms.HiddenInput,
            "longitude": forms.HiddenInput,
            "whatsapp": forms.TextInput(attrs={"inputmode": "tel", "placeholder": "024 412 3456"}),
            "website": forms.URLInput(attrs={"placeholder": "https://"}),
            "ghana_post_gps": forms.TextInput(attrs={"placeholder": "GA-123-4567", "autocapitalize": "characters"}),
        }

    def __init__(self, *args, plan, **kwargs):
        super().__init__(*args, **kwargs)
        self.plan = plan
        card = self.instance
        self.fields["visible"].initial = [k for k, _ in HIDEABLE_FIELDS if k not in (card.hidden_fields or [])]
        if card.accent in ACCENT_PRESETS.values():
            self.fields["accent_preset"].initial = card.accent
        else:
            self.fields["accent_preset"].initial = "custom"
            self.fields["accent_custom"].initial = card.accent
        self.fields["card_colour"].initial = card.card_colour or card.accent
        self.fields["card_colour"].widget.attrs["data-custom"] = "1" if card.card_colour else ""
        # Gate plan features (AC-10). Disabled fields keep their current value.
        self.locked = {}
        gates = {
            "logo_upload": plan.allow_logo,
            "remove_logo": plan.allow_logo,
            "background_upload": plan.allow_advanced_style,
            "remove_background": plan.allow_advanced_style,
            "button_style": plan.allow_advanced_style,
            "icon_layout": plan.allow_advanced_style,
            "template": plan.all_templates,
            "accent_custom": plan.custom_colours,
            "card_colour": plan.custom_colours,
            "hide_branding": plan.branding_footer == "removable",
        }
        for name, allowed in gates.items():
            if not allowed:
                self.fields[name].disabled = True
                self.locked[name] = True
        if not plan.custom_colours:
            self.fields["accent_preset"].choices = [(v, k.title()) for k, v in ACCENT_PRESETS.items()]

    def clean_whatsapp(self):
        value = self.cleaned_data.get("whatsapp")
        return normalise_phone(value) if value else ""

    def clean_ghana_post_gps(self):
        value = (self.cleaned_data.get("ghana_post_gps") or "").strip().upper()
        if value and not GPS_RE.match(value):
            raise ValidationError("Use the format GA-123-4567.")
        return value

    def clean_accent_custom(self):
        value = self.cleaned_data.get("accent_custom") or ""
        if value and not valid_hex(value):
            raise ValidationError("Choose a colour.")
        return value.lower()

    def clean_card_colour(self):
        value = self.cleaned_data.get("card_colour") or ""
        if value and not valid_hex(value):
            raise ValidationError("Choose a colour.")
        return value.lower()

    def _image(self, name, folder, max_px):
        upload = self.cleaned_data.get(name)
        if upload:
            return process_image(upload, folder=folder, max_px=max_px)
        return None

    def clean(self):
        cleaned = super().clean()
        for name, folder, size in (
            ("avatar_upload", "avatars", 640),
            ("logo_upload", "logos", 400),
            ("background_upload", "backgrounds", 1200),
        ):
            try:
                cleaned[name] = self._image(name, folder, size)
            except ValidationError as exc:
                self.add_error(name, exc)
        return cleaned

    def save(self, commit=True):
        card = super().save(commit=False)
        data = self.cleaned_data
        preset = data.get("accent_preset")
        if preset == "custom" and data.get("accent_custom") and self.plan.custom_colours:
            card.accent = data["accent_custom"]
        elif preset and preset != "custom":
            card.accent = preset
        if self.plan.custom_colours:
            # An untouched picker (still showing the old card colour) or a band
            # equal to the card colour means "follow the card colour".
            band = (data.get("card_colour") or "").lower()
            untouched = not card.card_colour and band == (self.fields["card_colour"].initial or "").lower()
            card.card_colour = "" if untouched or band in ("", card.accent.lower()) else band
        card.hidden_fields = [k for k, _ in HIDEABLE_FIELDS if k not in (data.get("visible") or [])]
        for flag, upload, field in (
            ("remove_avatar", "avatar_upload", "avatar"),
            ("remove_logo", "logo_upload", "logo"),
            ("remove_background", "background_upload", "background"),
        ):
            new_file = data.get(upload)
            if new_file:
                old = getattr(card, field)
                if old:
                    old.delete(save=False)
                getattr(card, field).save(new_file.name, new_file, save=False)
            elif data.get(flag) and getattr(card, field):
                getattr(card, field).delete(save=False)
                setattr(card, field, "")
        if commit:
            card.save()
        return card


class PhoneForm(forms.ModelForm):
    class Meta:
        model = CardPhone
        fields = ["number", "label"]
        labels = {"number": "Phone number", "label": "Label"}
        widgets = {
            "number": forms.TextInput(attrs={"inputmode": "tel", "placeholder": "024 412 3456"}),
            "label": forms.TextInput(attrs={"placeholder": "MTN, Telecel, Office…"}),
        }

    def clean_number(self):
        return normalise_phone(self.cleaned_data["number"])


PhoneFormSet = inlineformset_factory(
    Card, CardPhone, form=PhoneForm, extra=0, max_num=3, validate_max=True, can_delete=True,
    min_num=1, validate_min=True,
)


class SlugForm(forms.Form):
    slug = forms.CharField(
        label="Your card link", max_length=40,
        help_text="3 to 40 lowercase letters, numbers and hyphens.",
    )


class SocialLinkForm(forms.ModelForm):
    class Meta:
        model = SocialLink
        fields = ["platform", "url"]
        labels = {"platform": "Platform", "url": "Link"}
        widgets = {"url": forms.URLInput(attrs={"placeholder": "https://"})}


class NewCardForm(forms.Form):
    full_name = forms.CharField(label="Full name on the new card", max_length=120)
    job_title = forms.CharField(label="Position", max_length=120, required=False)
