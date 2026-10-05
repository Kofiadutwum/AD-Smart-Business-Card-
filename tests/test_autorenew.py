"""Automatic yearly renewal by card: set-up at checkout, advance notice, the charge, retries, cancelling."""

from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from apps.billing import paystack, services
from apps.billing.models import Payment, Subscription

from .helpers import BaseTest


class AutoRenewTests(BaseTest):
    def setUp(self):
        self.user = self.make_user()
        self.make_card(self.user)

    def card_subscription(self, **fields):
        """A customer whose plan renews automatically and ends ``days`` from now (negative: already ended)."""
        days = fields.pop("days", 3)
        sub = self.subscribe(self.user, self.pro, days=days)
        sub.auto_renew = True
        sub.authorization_code = "AUTH_test123"
        sub.authorization_email = self.user.email
        sub.card_brand, sub.card_last4, sub.card_expiry = "visa", "4081", "12/2030"
        for key, value in fields.items():
            setattr(sub, key, value)
        sub.save()
        return sub

    # ------------------------------------------------------------------ set-up at checkout
    def pay(self, action):
        self.client.force_login(self.user)
        response = self.client.post("/billing/", {"action": action, "plan": "professional", "accept_policy": "1"})
        payment = Payment.objects.latest("created_at")
        return payment, response

    def test_card_payment_keeps_the_card_for_automatic_renewal(self):
        payment, _ = self.pay("pay")
        self.assertTrue(payment.set_up_auto_renew)
        self.client.get(f"/billing/callback?reference={payment.reference}")
        sub = Subscription.objects.get(user=self.user)
        self.assertTrue(sub.renews_automatically)
        self.assertEqual(sub.card_last4, "4081")
        self.assertEqual(sub.card_label, "Visa ending 4081")
        self.assertTrue(sub.authorization_code.startswith("AUTH_"))

    def test_mobile_money_payment_does_not_renew_itself(self):
        payment, _ = self.pay("pay_momo")
        self.assertFalse(payment.set_up_auto_renew)
        self.client.get(f"/billing/callback?reference={payment.reference}")
        sub = Subscription.objects.get(user=self.user)
        self.assertFalse(sub.auto_renew)
        self.assertEqual(sub.authorization_code, "")

    def test_checkout_page_offers_card_first_and_a_small_mobile_money_button(self):
        self.client.force_login(self.user)
        html = self.client.get("/billing/").content.decode()
        self.assertIn('value="pay"', html)
        self.assertIn('value="pay_momo"', html)
        self.assertLess(html.index('value="pay"'), html.index('value="pay_momo"'))
        self.assertNotIn("renews every year", html.lower())

    def test_the_payment_page_is_narrowed_to_the_chosen_way_to_pay(self):
        with mock.patch.object(paystack, "initialize", wraps=paystack.initialize) as initialize:
            self.pay("pay")
            self.assertEqual(initialize.call_args.kwargs["channels"], ["card"])
            self.pay("pay_momo")
            self.assertEqual(initialize.call_args.kwargs["channels"], ["mobile_money"])

    def test_a_card_that_cannot_be_charged_again_is_not_kept(self):
        payment, _ = self.pay("pay")
        data = paystack.verify(payment.reference, expected_amount=payment.amount_minor, expected_currency="GHS")
        data["authorization"]["reusable"] = False
        with mock.patch.object(paystack, "verify", return_value=data):
            self.client.get(f"/billing/callback?reference={payment.reference}")
        self.assertFalse(Subscription.objects.get(user=self.user).auto_renew)

    # ------------------------------------------------------------------ what customers are sent
    def test_reminders_skip_automatic_renewals_but_not_the_others(self):
        self.card_subscription(days=7)
        other = self.make_user(email="other@example.com")
        self.subscribe(other, self.pro, days=7)
        call_command("run_daily_jobs", "--only", "reminders", verbosity=0)
        recipients = [m.to[0] for m in mail.outbox]
        self.assertIn("other@example.com", recipients)
        self.assertNotIn(self.user.email, recipients)

    def test_advance_notice_a_week_before_with_the_amount_and_only_once(self):
        self.card_subscription(days=6)
        self.assertEqual(services.send_renewal_notices(), 1)
        message = mail.outbox[-1]
        self.assertEqual(message.to, [self.user.email])
        self.assertIn("Visa ending 4081", message.body)
        self.assertIn("350.00", message.body)
        self.assertIn("turn off automatic renewal", message.body)
        self.assertEqual(services.send_renewal_notices(), 0)

    def test_advance_notice_not_sent_early_or_to_manual_renewals(self):
        self.card_subscription(days=40)
        self.assertEqual(services.send_renewal_notices(), 0)
        Subscription.objects.update(auto_renew=False, expires_at=timezone.now() + timedelta(days=3))
        self.assertEqual(services.send_renewal_notices(), 0)

    def test_notice_says_when_the_price_changed(self):
        self.card_subscription(days=5)
        Payment.objects.create(
            user=self.user, reference="OLD-1", purpose=Payment.PURPOSE_SUBSCRIPTION, plan=self.pro, months=12,
            amount_minor=30000, subtotal_minor=30000, status=Payment.SUCCESS, paid_at=timezone.now() - timedelta(days=360),
        )
        services.send_renewal_notices()
        self.assertIn("price has changed", mail.outbox[-1].body)

    # ------------------------------------------------------------------ the charge
    def test_due_plan_is_renewed_from_its_end_date_and_charged_once_a_day(self):
        sub = self.card_subscription(days=0, expires_at=timezone.now() - timedelta(hours=5))
        old_end = sub.expires_at
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(services.attempt_renewal(sub.pk), "renewed")
        sub.refresh_from_db()
        self.assertGreater(sub.expires_at, old_end + timedelta(days=360))
        self.assertLess(sub.expires_at, old_end + timedelta(days=370))
        payment = Payment.objects.get(is_auto_renewal=True)
        self.assertEqual(payment.status, Payment.SUCCESS)
        self.assertEqual(payment.amount_minor, 35000)
        self.assertEqual(sub.renew_attempts, 0)
        self.assertTrue(any("Your plan has renewed" in m.subject for m in mail.outbox))
        self.assertIn("automatic yearly renewal", mail.outbox[-1].body)
        # nothing more is charged until the next year
        self.assertIsNone(services.attempt_renewal(sub.pk))
        self.assertEqual(Payment.objects.filter(is_auto_renewal=True).count(), 1)

    def test_nothing_is_charged_before_the_plan_ends_or_after_cancelling(self):
        sub = self.card_subscription(days=2)
        self.assertIsNone(services.attempt_renewal(sub.pk))
        sub.expires_at = timezone.now() - timedelta(hours=1)
        sub.save()
        services.cancel_auto_renew(sub)
        self.assertIsNone(services.attempt_renewal(sub.pk))
        self.assertFalse(Payment.objects.filter(is_auto_renewal=True).exists())

    def test_daily_job_renews_and_reports(self):
        self.card_subscription(days=0, expires_at=timezone.now() - timedelta(hours=2))
        call_command("run_daily_jobs", "--only", "auto_renewals", verbosity=0)
        self.assertEqual(Payment.objects.filter(is_auto_renewal=True, status=Payment.SUCCESS).count(), 1)

    @override_settings(PAYMENT_SANDBOX_OUTCOME="failed")
    def test_failed_charge_is_retried_on_days_one_three_five_then_stops(self):
        sub = self.card_subscription(days=0, expires_at=timezone.now() - timedelta(hours=1))
        start = sub.expires_at + timedelta(hours=2)
        # day 0, 1, 3 and 5 after the end date: four tries, and nothing extra on the days in between
        days = (0, 0, 1, 2, 3, 5)
        outcomes = [services.attempt_renewal(sub.pk, now=start + timedelta(days=d)) for d in days]
        self.assertEqual(outcomes, ["failed", None, "failed", None, "failed", "failed"])
        sub.refresh_from_db()
        self.assertFalse(sub.auto_renew)
        self.assertEqual(sub.authorization_code, "")
        failed = [m for m in mail.outbox if "could not renew" in m.subject]
        self.assertEqual(len(failed), 4)
        self.assertIn("try again on", failed[0].body)
        self.assertIn("automatic renewal has stopped", failed[-1].body)

    @override_settings(PAYMENT_SANDBOX_OUTCOME="failed")
    def test_grace_notice_waits_until_automatic_renewal_has_given_up(self):
        sub = self.card_subscription(days=0, expires_at=timezone.now() - timedelta(days=1))
        call_command("run_daily_jobs", "--only", "expiry_notices", verbosity=0)
        self.assertFalse(any("grace period" in m.subject for m in mail.outbox))
        Subscription.objects.filter(pk=sub.pk).update(auto_renew=False)
        call_command("run_daily_jobs", "--only", "expiry_notices", verbosity=0)
        self.assertTrue(any("grace period" in m.subject for m in mail.outbox))

    # ------------------------------------------------------------------ cancelling
    def test_customer_can_cancel_and_keeps_the_plan_until_it_ends(self):
        sub = self.card_subscription(days=100)
        end = sub.expires_at
        self.client.force_login(self.user)
        response = self.client.post("/billing/auto-renew/cancel", follow=True)
        self.assertContains(response, "Automatic renewal is off")
        sub.refresh_from_db()
        self.assertFalse(sub.auto_renew)
        self.assertEqual(sub.authorization_code, "")
        self.assertEqual(sub.expires_at, end)
        self.assertTrue(sub.is_live)
        self.assertTrue(any(m.subject == "Automatic renewal is off" for m in mail.outbox))

    def test_cancel_needs_a_post_and_a_signed_in_customer(self):
        self.card_subscription(days=100)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/billing/auto-renew/cancel").status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post("/billing/auto-renew/cancel").status_code, 302)
        self.assertTrue(Subscription.objects.get(user=self.user).auto_renew)

    def test_dashboard_shows_whether_the_plan_renews_itself(self):
        self.card_subscription(days=100)
        self.client.force_login(self.user)
        html = self.client.get("/dashboard/").content.decode()
        self.assertIn("Automatic renewal", html)
        self.assertIn("Visa ending 4081", html)

    def test_the_card_token_is_never_shown_to_the_customer(self):
        self.card_subscription(days=100)
        self.client.force_login(self.user)
        for path in ("/billing/", "/dashboard/", "/billing/receipts"):
            self.assertNotContains(self.client.get(path), "AUTH_test123")
