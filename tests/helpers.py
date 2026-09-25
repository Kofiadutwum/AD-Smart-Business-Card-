from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import Plan, Subscription
from apps.cards.services import create_card


@override_settings(PAYMENT_SANDBOX=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class BaseTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_initial", "--offline", verbosity=0)
        cls.basic = Plan.objects.get(code="basic")
        cls.pro = Plan.objects.get(code="professional")
        cls.business = Plan.objects.get(code="business")

    def make_user(self, email="kwame@example.com", password="Str0ng-pass-123", verified=True, **extra):
        user = User.objects.create_user(
            email=email, password=password, full_name=extra.pop("full_name", "Kwame Mensah"),
            email_verified_at=timezone.now() if verified else None, **extra,
        )
        return user

    def make_card(self, user, name="Kwame Mensah", **fields):
        return create_card(user, name, **fields)

    def subscribe(self, user, plan=None, days=200):
        sub = Subscription.objects.create(
            user=user, plan=plan or self.basic, started_at=timezone.now(), expires_at=timezone.now() + timedelta(days=days)
        )
        from apps.billing.services import activate_cards

        activate_cards(user, sub)
        return sub
