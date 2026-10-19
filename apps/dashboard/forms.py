"""Card editor forms (Sections 3.1, 12, PRV-01)."""

import re

from django import forms
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.forms import inlineformset_factory

from apps.cards.colors import valid_hex
from apps.cards.models import (
    ACCENT_PRESETS, HIDEABLE_FIELDS, PRODUCT_IMAGE_BYTES, BusinessPage, BusinessProduct, BusinessReview,
    BusinessService, Card, CardPhone, SocialLink, accent_choices,
)
from apps.core.utils import normalise_phone, process_image
from django.utils.translation import gettext_lazy as _

GPS_RE = re.compile(r"^[A-Z]{2}-\d{1,4}-\d{3,4}$")


class CardForm(forms.ModelForm):
    avatar_upload = forms.ImageField(
        label=_("Profile photo"), required=False, help_text=_("JPG, PNG or WebP up to 5 MB. We resize it for fast loading.")
    )
    remove_avatar = forms.BooleanField(label=_("Remove photo"), required=False)
    logo_upload = forms.ImageField(label=_("Business logo"), required=False, help_text=_("PNG with a transparent background works best."))
    remove_logo = forms.BooleanField(label=_("Remove logo"), required=False)
    background_upload = forms.ImageField(label=_("Background image"), required=False)
    remove_background = forms.BooleanField(label=_("Remove background"), required=False)
    accent_preset = forms.ChoiceField(
        label=_("Card colour"), choices=accent_choices() + [("custom", _("Custom"))],
        widget=forms.RadioSelect, required=False,
    )
    accent_custom = forms.CharField(label=_("Custom colour"), required=False, widget=forms.TextInput(attrs={"type": "color"}))
    card_colour = forms.CharField(label=_("Band colour"), required=False, widget=forms.TextInput(attrs={"type": "color"}))
    visible = forms.MultipleChoiceField(
        label=_("Show on my public card"), choices=HIDEABLE_FIELDS, widget=forms.CheckboxSelectMultiple, required=False
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
            "full_name": _("Full name"),
            "job_title": _("Position / job title"),
            "business_name": _("Business name"),
            "bio": _("Short bio"),
            "whatsapp": _("WhatsApp number"),
            "email": _("Email address"),
            "website": _("Website"),
            "address": _("Physical address"),
            "ghana_post_gps": _("Ghana Post GPS address"),
            "template": _("Layout"),
            "button_style": _("Button style"),
            "icon_layout": _("Social links"),
            "hide_branding": _("Remove “Powered by AD Smart”"),
            "allow_indexing": _("Let search engines show my card"),
            "collect_leads": _("Let visitors send me their details"),
            "is_published": _("Card is visible to the public"),
        }
        help_texts = {
            "bio": _("Up to 300 characters."),
            "whatsapp": _("Can differ from your call number."),
            "ghana_post_gps": _("For example GA-123-4567."),
            "allow_indexing": _("Off by default, so your card only reaches people you share it with."),
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
            self.fields["accent_preset"].choices = accent_choices()

    def clean_whatsapp(self):
        value = self.cleaned_data.get("whatsapp")
        return normalise_phone(value) if value else ""

    def clean_ghana_post_gps(self):
        value = (self.cleaned_data.get("ghana_post_gps") or "").strip().upper()
        if value and not GPS_RE.match(value):
            raise ValidationError(_("Use the format GA-123-4567."))
        return value

    def clean_accent_custom(self):
        value = self.cleaned_data.get("accent_custom") or ""
        if value and not valid_hex(value):
            raise ValidationError(_("Choose a colour."))
        return value.lower()

    def clean_card_colour(self):
        value = self.cleaned_data.get("card_colour") or ""
        if value and not valid_hex(value):
            raise ValidationError(_("Choose a colour."))
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
        labels = {"number": _("Phone number"), "label": _("Label")}
        widgets = {
            "number": forms.TextInput(attrs={"inputmode": "tel", "placeholder": "024 412 3456"}),
            "label": forms.TextInput(attrs={"placeholder": _("MTN, Telecel, Office…")}),
        }

    def clean_number(self):
        return normalise_phone(self.cleaned_data["number"])


PhoneFormSet = inlineformset_factory(
    Card, CardPhone, form=PhoneForm, extra=0, max_num=3, validate_max=True, can_delete=True,
    min_num=1, validate_min=True,
)


class SlugForm(forms.Form):
    slug = forms.CharField(
        label=_("Your card link"), max_length=40,
        help_text=_("3 to 40 lowercase letters, numbers and hyphens."),
    )


class SocialLinkForm(forms.ModelForm):
    class Meta:
        model = SocialLink
        fields = ["platform", "url"]
        labels = {"platform": _("Platform"), "url": _("Link")}
        widgets = {"url": forms.URLInput(attrs={"placeholder": "https://"})}


class NewCardForm(forms.Form):
    full_name = forms.CharField(label=_("Full name on the new card"), max_length=120)
    job_title = forms.CharField(label=_("Position"), max_length=120, required=False)


# --------------------------------------------------------------------------
# Business page
# --------------------------------------------------------------------------


class BusinessPageForm(forms.ModelForm):
    class Meta:
        model = BusinessPage
        fields = ["is_on", "tagline", "about", "hours"]
        labels = {
            "is_on": _("Show my business page to everyone"),
            "tagline": _("Tagline"),
            "about": _("About your business"),
            "hours": _("Opening hours"),
        }
        help_texts = {
            "is_on": _("When it is on, an “Explore Business” button appears on your card."),
            "tagline": _("One short line under your business name."),
            "hours": _("One line for each day or group of days, for example Mon–Fri 8am–6pm."),
        }
        widgets = {"about": forms.Textarea(attrs={"rows": 6}), "hours": forms.Textarea(attrs={"rows": 4})}


class ProductForm(forms.ModelForm):
    class Meta:
        model = BusinessProduct
        fields = ["image", "title", "price", "description"]
        labels = {
            "image": _("Picture"), "title": _("Name"), "price": _("Price (optional)"), "description": _("Description (optional)"),
        }
        help_texts = {
            "image": _("JPG, PNG or WebP. We shrink every picture to 50 KB or less so your page opens fast."),
            "price": _("Write it the way you want it shown, for example GHS 120 or From GHS 50."),
        }
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["image"].required = False  # keep the current picture unless a new one is chosen

    def clean_image(self):
        upload = self.cleaned_data.get("image")
        if isinstance(upload, UploadedFile):
            return process_image(upload, folder="products", max_px=1000, target_bytes=PRODUCT_IMAGE_BYTES)
        return upload


class ServiceForm(forms.ModelForm):
    class Meta:
        model = BusinessService
        fields = ["title", "description"]
        labels = {"title": _("Service"), "description": _("Description (optional)")}
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class ReviewForm(forms.ModelForm):
    rating = forms.TypedChoiceField(
        label=_("Stars (optional)"), required=False, coerce=int, empty_value=None,
        choices=[("", "—")] + [(n, "★" * n + "☆" * (5 - n)) for n in range(5, 0, -1)],
    )

    class Meta:
        model = BusinessReview
        fields = ["author", "text", "rating"]
        labels = {"author": _("Customer name"), "text": _("What they said")}
        help_texts = {"text": _("Only add words your customer has agreed to share.")}
        widgets = {"text": forms.Textarea(attrs={"rows": 3})}
