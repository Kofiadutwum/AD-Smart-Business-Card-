from django import forms

from .models import CardReport, Lead


class LeadForm(forms.ModelForm):
    """What a card visitor sends back to the owner."""

    class Meta:
        model = Lead
        fields = ["name", "phone", "email", "organisation", "note"]
        labels = {
            "name": "Your name",
            "phone": "Phone",
            "email": "Email",
            "organisation": "Company",
            "note": "Message",
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
            raise forms.ValidationError("Add a phone number or an email so they can reach you.")
        return cleaned


class ReportForm(forms.ModelForm):
    class Meta:
        model = CardReport
        fields = ["reason", "details", "reporter_email"]
        labels = {
            "reason": "What is wrong with this card?",
            "details": "Details",
            "reporter_email": "Your email (optional, if you would like a reply)",
        }
        widgets = {"reason": forms.RadioSelect, "details": forms.Textarea(attrs={"rows": 4})}
