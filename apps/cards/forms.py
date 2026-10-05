from django import forms

from apps.accounts.forms import RegisterForm

from .models import CardReport, Lead
from django.utils.translation import gettext_lazy as _


class LeadForm(forms.ModelForm):
    """What a card visitor sends back to the owner."""

    class Meta:
        model = Lead
        fields = ["name", "phone", "email", "organisation", "note"]
        labels = {
            "name": _("Your name"),
            "phone": _("Phone"),
            "email": _("Email"),
            "organisation": _("Company"),
            "note": _("Message"),
        }
        widgets = {
            "name": forms.TextInput(attrs={"autocomplete": "name"}),
            "phone": forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}),
            "email": forms.EmailInput(attrs={"autocomplete": "email"}),
            "organisation": forms.TextInput(attrs={"autocomplete": "organization"}),
            "note": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("phone") and not cleaned.get("email"):
            raise forms.ValidationError(_("Add a phone number or an email so they can reach you."))
        return cleaned


class ReportForm(forms.ModelForm):
    class Meta:
        model = CardReport
        fields = ["reason", "details", "reporter_email"]
        labels = {
            "reason": _("What is wrong with this card?"),
            "details": _("Details"),
            "reporter_email": _("Your email (optional, if you would like a reply)"),
        }
        widgets = {"reason": forms.RadioSelect, "details": forms.Textarea(attrs={"rows": 4})}


class PinForm(forms.Form):
    """The PIN printed on the back of a pre-made card."""

    pin = forms.CharField(
        label=_("PIN on the back of your card"),
        max_length=12,
        help_text=_("Eight letters and numbers, for example K7M2-X9QA."),
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off", "autocapitalize": "characters", "spellcheck": "false",
                "placeholder": "XXXX-XXXX", "inputmode": "text", "class": "pin-input",
            }
        ),
    )


class ClaimForm(RegisterForm):
    """A new account for the buyer, plus two optional lines for the card."""

    job_title = forms.CharField(label=_("Job title"), max_length=120, required=False)
    business_name = forms.CharField(label=_("Business or organisation"), max_length=160, required=False)

    field_order = ["full_name", "job_title", "business_name", "email", "phone", "password", "accept_terms"]
