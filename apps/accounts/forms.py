from django import forms
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError

from apps.core.utils import normalise_phone

from .models import User
from django.utils.translation import gettext_lazy as _


class RegisterForm(forms.Form):
    full_name = forms.CharField(label=_("Full name"), max_length=120, widget=forms.TextInput(attrs={"autocomplete": "name"}))
    email = forms.EmailField(label=_("Email address"), widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    phone = forms.CharField(
        label=_("Mobile number"),
        help_text=_("We use it only to reach you about orders and delivery."),
        widget=forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel", "placeholder": "024 412 3456"}),
    )
    password = forms.CharField(
        label=_("Password"),
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text=_("At least 8 characters. Avoid common words and all-number passwords."),
    )
    accept_terms = forms.BooleanField(
        label=_("I accept the Terms of Service and Privacy Policy"),
        error_messages={"required": _("You need to accept the terms to create an account.")},
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError(_("That email already has an account. Sign in instead."))
        return email

    def clean_phone(self):
        phone = normalise_phone(self.cleaned_data["phone"])
        if User.objects.filter(phone=phone).exists():
            raise ValidationError(_("That number is linked to another account. Sign in or recover that account."))
        return phone

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password")
        if password:
            candidate = User(email=cleaned.get("email", ""), full_name=cleaned.get("full_name", ""))
            try:
                password_validation.validate_password(password, candidate)
            except ValidationError as exc:
                self.add_error("password", exc)
        return cleaned


class LoginForm(forms.Form):
    email = forms.EmailField(label=_("Email address"), widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    password = forms.CharField(label=_("Password"), widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    remember = forms.BooleanField(label=_("Keep me signed in"), required=False)


class RecoveryRequestForm(forms.Form):
    email = forms.EmailField(label=_("Email address"), widget=forms.EmailInput(attrs={"autocomplete": "email"}))


class RecoveryVerifyForm(forms.Form):
    code = forms.RegexField(
        label=_("6-digit code"),
        regex=r"^\s*\d{6}\s*$",
        error_messages={"invalid": _("Enter the 6 digits from the email.")},
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "maxlength": 6}),
    )
    password = forms.CharField(label=_("New password"), widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password_confirm = forms.CharField(
        label=_("Confirm new password"), widget=forms.PasswordInput(attrs={"autocomplete": "new-password"})
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") and cleaned.get("password") != cleaned.get("password_confirm"):
            self.add_error("password_confirm", _("The two passwords do not match."))
        if cleaned.get("password"):
            try:
                password_validation.validate_password(cleaned["password"])
            except ValidationError as exc:
                self.add_error("password", exc)
        return cleaned


class PasswordChangeForm(forms.Form):
    current_password = forms.CharField(
        label=_("Current password"), widget=forms.PasswordInput(attrs={"autocomplete": "current-password"})
    )
    password = forms.CharField(label=_("New password"), widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password_confirm = forms.CharField(
        label=_("Confirm new password"), widget=forms.PasswordInput(attrs={"autocomplete": "new-password"})
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        value = self.cleaned_data["current_password"]
        if not self.user.check_password(value):
            raise ValidationError(_("That is not your current password."))
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") and cleaned.get("password") != cleaned.get("password_confirm"):
            self.add_error("password_confirm", _("The two passwords do not match."))
        if cleaned.get("password"):
            try:
                password_validation.validate_password(cleaned["password"], self.user)
            except ValidationError as exc:
                self.add_error("password", exc)
        return cleaned


class AccountForm(forms.ModelForm):
    phone = forms.CharField(label=_("Mobile number"), widget=forms.TextInput(attrs={"inputmode": "tel"}))

    class Meta:
        model = User
        fields = ["full_name", "phone", "company_name", "company_address"]
        labels = {
            "full_name": "Your name",
            "company_name": "Company name for receipts",
            "company_address": "Company address for receipts",
        }

    def clean_phone(self):
        phone = normalise_phone(self.cleaned_data["phone"])
        if User.objects.filter(phone=phone).exclude(pk=self.instance.pk).exists():
            raise ValidationError(_("That number is linked to another account."))
        return phone
