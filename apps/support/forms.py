from django import forms

from .models import SupportRequest


class ContactForm(forms.ModelForm):
    message = forms.CharField(label="How can we help?", widget=forms.Textarea(attrs={"rows": 5}), max_length=4000)

    class Meta:
        model = SupportRequest
        fields = ["name", "email", "phone", "category", "subject"]
        labels = {"name": "Your name", "phone": "Phone (optional)", "category": "Topic"}
        widgets = {
            "name": forms.TextInput(attrs={"autocomplete": "name"}),
            "email": forms.EmailInput(attrs={"autocomplete": "email"}),
            "phone": forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}),
        }


class ReplyForm(forms.Form):
    body = forms.CharField(label="Your reply", widget=forms.Textarea(attrs={"rows": 4}), max_length=4000)
