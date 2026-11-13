"""Accounts: customers and administrators share one user table.

Administrators are users with ``is_staff`` and a ``staff_role`` (Section 14).
"""

from datetime import timedelta

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone

EMAIL_CHANGE_DAYS = 90  # a customer may change their email this often
EMAIL_FIX_WINDOW = 24  # hours after a change in which a typo may be corrected


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("An email address is required.")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("staff_role", User.ROLE_SUPER)
        extra.setdefault("email_verified_at", timezone.now())
        return self._create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    ROLE_SUPER = "super"
    ROLE_FINANCE = "finance"
    ROLE_SUPPORT = "support"
    ROLE_DESIGN = "design"
    ROLE_OPS = "ops"
    STAFF_ROLES = [
        (ROLE_SUPER, "Main / Super Admin"),
        (ROLE_FINANCE, "Financial Manager"),
        (ROLE_SUPPORT, "Customer Support"),
        (ROLE_DESIGN, "Design Manager"),
        (ROLE_OPS, "Operations Manager"),
    ]

    # Django's default of 128 is too short for the Flask site's scrypt hashes ("werkzeug$" + 162 characters).
    password = models.CharField("password", max_length=255)
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=20, unique=True, null=True, blank=True)
    google_sub = models.CharField(max_length=255, unique=True, null=True, blank=True)

    # Email confirmation was removed (the form checks the address as it is typed). These two are kept as history
    # of the old flow and are no longer read.
    email_verified_at = models.DateTimeField(null=True, blank=True)
    verification_waived_at = models.DateTimeField(null=True, blank=True)
    email_changed_at = models.DateTimeField(null=True, blank=True)
    accepted_terms_at = models.DateTimeField(null=True, blank=True)
    # Set when support gives the customer a temporary password: they must choose their own before using the
    # account, and the temporary one stops working at this time. Empty for everyone else.
    password_temp_until = models.DateTimeField(null=True, blank=True)

    # Display preferences, remembered across devices (empty = follow the browser / dollars).
    currency = models.CharField(max_length=3, blank=True)
    language = models.CharField(max_length=8, blank=True)

    # Receipt/invoice details for business customers (PAY-09).
    company_name = models.CharField(max_length=160, blank=True)
    company_address = models.CharField(max_length=255, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    staff_role = models.CharField(max_length=16, choices=STAFF_ROLES, blank=True)
    totp_secret = models.CharField(max_length=64, blank=True)
    totp_confirmed_at = models.DateTimeField(null=True, blank=True)

    is_suspended = models.BooleanField(default=False)
    suspension_reason = models.TextField(blank=True)

    deletion_requested_at = models.DateTimeField(null=True, blank=True)
    anonymised_at = models.DateTimeField(null=True, blank=True)

    date_joined = models.DateTimeField(default=timezone.now)

    # Primary key of this user on the old Flask site, for the one-off import.
    legacy_id = models.IntegerField(unique=True, null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ["-date_joined"]

    def __str__(self):
        return self.email

    # --- identity -----------------------------------------------------------

    @property
    def display_name(self):
        if self.full_name:
            return self.full_name
        card = self.cards.filter(deleted_at__isnull=True).first()
        return card.full_name if card else self.email

    @property
    def first_name(self):
        return (self.display_name or "").split(" ")[0]

    @property
    def can_change_email(self):
        """Once every 90 days, but a slip can be fixed for 24 hours after a change."""
        if not self.email_changed_at:
            return True
        age = timezone.now() - self.email_changed_at
        return age < timedelta(hours=EMAIL_FIX_WINDOW) or age >= timedelta(days=EMAIL_CHANGE_DAYS)

    @property
    def next_email_change(self):
        return self.email_changed_at + timedelta(days=EMAIL_CHANGE_DAYS)

    # --- staff --------------------------------------------------------------

    @property
    def role(self):
        if self.is_superuser:
            return self.ROLE_SUPER
        return self.staff_role if self.is_staff else ""

    @property
    def role_label(self):
        return dict(self.STAFF_ROLES).get(self.role, "")

    @property
    def has_totp(self):
        return bool(self.totp_secret and self.totp_confirmed_at)

    # --- subscription shortcuts --------------------------------------------

    @property
    def subscription(self):
        return getattr(self, "subscription_record", None)

    @property
    def update_card(self):
        """This customer's pay-when-you-update card, if any: live with no plan of its own to pay for."""
        return next((card for card in self.cards.all() if card.update_plan_id and not card.deleted_at), None)


class OneTimeCode(models.Model):
    """A 6-digit code sent by email, for password recovery (REG-06, REG-07).

    Only a hash of the code is stored.
    """

    PURPOSE_PASSWORD_RESET = "password_reset"
    PURPOSE_STAFF_LOGIN = "staff_login"
    PURPOSES = [(PURPOSE_PASSWORD_RESET, "Password reset"), (PURPOSE_STAFF_LOGIN, "Staff sign-in")]
    MAX_ATTEMPTS = 5

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="one_time_codes")
    purpose = models.CharField(max_length=32, choices=PURPOSES)
    code_hash = models.CharField(max_length=255)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def set_code(self, code):
        self.code_hash = make_password(code)

    @property
    def is_usable(self):
        return (
            self.used_at is None
            and self.attempts < self.MAX_ATTEMPTS
            and timezone.now() < self.expires_at
        )

    def check_code(self, code):
        """Count the attempt, then compare. Callers must save afterwards."""
        if not self.is_usable:
            return False
        self.attempts += 1
        return check_password(code, self.code_hash)
