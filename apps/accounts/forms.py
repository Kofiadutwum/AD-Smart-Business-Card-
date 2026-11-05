from django import forms
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError

from apps.core.utils import normalise_phone
from django.urls import reverse_lazy

from .models import User
from .services import email_problem
from django.utils.translation import gettext_lazy as _

# The email box asks /auth/check-email as the customer types (static/js/email-check.js).
EMAIL_CHECK_ATTRS = {"autocomplete": "email", "data-email-check": reverse_lazy("accounts:check_email")}


class RegisterForm(forms.Form):
    full_name = forms.CharField(label=_("Full name"), max_length=120, widget=forms.TextInput(attrs={"autocomplete": "name"}))
    email = forms.EmailField(label=_("Email address"), widget=forms.EmailInput(attrs=EMAIL_CHECK_ATTRS))
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
        problem = email_problem(email)
        if problem:
            raise ValidationError(problem)
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


class OneTimePasswordForm(RecoveryVerifyForm):
    """A customer who lost their email: the phone number on the account, the one-time password support read out, and a new password."""

    phone = forms.CharField(
        label=_("Phone number on your account"),
        widget=forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel", "placeholder": "024 412 3456"}),
    )
    code = forms.CharField(
        label=_("One-time password from support"),
        widget=forms.TextInput(attrs={"autocomplete": "off", "autocapitalize": "characters", "placeholder": "XXXXX-XXXXX"}),
    )
    field_order = ["phone", "code", "password", "password_confirm"]

    def clean_phone(self):
        return normalise_phone(self.cleaned_data["phone"])

    def clean_code(self):
        raw = "".join(ch for ch in self.cleaned_data["code"].upper() if ch.isalnum())
        return f"{raw[:5]}-{raw[5:]}"


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


class EmailChangeForm(forms.Form):
    email = forms.EmailField(label=_("New email address"), widget=forms.EmailInput(attrs=EMAIL_CHECK_ATTRS))
    current_password = forms.CharField(
        label=_("Current password"), widget=forms.PasswordInput(attrs={"autocomplete": "current-password"})
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        problem = email_problem(email, self.user)
        if problem:
            raise ValidationError(problem)
        return email

    def clean_current_password(self):
        value = self.cleaned_data["current_password"]
        if not self.user.check_password(value):
            raise ValidationError(_("That is not your current password."))
        return value


class AccountForm(forms.ModelForm):
    phone = forms.CharField(label=_("Mobile number"), widget=forms.TextInput(attrs={"inputmode": "tel"}))

    class Meta:
        model = User
        fields = ["full_name", "phone", "company_name", "company_address"]
        labels = {
            "full_name": _("Your name"),
            "company_name": _("Company name for receipts"),
            "company_address": _("Company address for receipts"),
        }

    def clean_phone(self):
        phone = normalise_phone(self.cleaned_data["phone"])
        if User.objects.filter(phone=phone).exclude(pk=self.instance.pk).exists():
            raise ValidationError(_("That number is linked to another account."))
        return phone
