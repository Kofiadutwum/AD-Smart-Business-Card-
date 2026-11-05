from django import forms

from .models import SupportRequest
from django.utils.translation import gettext_lazy as _


class ContactForm(forms.ModelForm):
    message = forms.CharField(label=_("How can we help?"), widget=forms.Textarea(attrs={"rows": 5}), max_length=4000)
    consent = forms.BooleanField(
        label=_("I agree to AD Smart keeping my details to answer this request, as set out in the Privacy Policy."),
        error_messages={"required": _("Please tick the box to agree before sending your message.")},
    )

    class Meta:
        model = SupportRequest
        fields = ["name", "email", "phone", "category", "subject"]
        labels = {
            "name": _("Your name"), "email": _("Email"), "phone": _("Phone (optional)"),
            "category": _("Topic"), "subject": _("Subject"),
        }
        widgets = {
            "name": forms.TextInput(attrs={"autocomplete": "name"}),
            "email": forms.EmailInput(attrs={"autocomplete": "email"}),
            "phone": forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}),
        }


class ReplyForm(forms.Form):
    body = forms.CharField(label=_("Your reply"), widget=forms.Textarea(attrs={"rows": 4}), max_length=4000)
