"""End-to-end flows: sign-up, recovery, payments, webhooks, NFC orders, staff access, daily jobs."""

import hashlib
import hmac
import json
import re
from datetime import timedelta
from unittest import mock

import pyotp
from django.core import mail
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import OneTimeCode, User
from apps.billing import services as billing
from apps.billing.models import Payment, Subscription
from apps.core.models import AuditLog, EmailLog
from apps.nfc import services as nfc
from apps.nfc.models import DeliveryZone, NFCOrder

from .helpers import BaseTest


class AccountFlowTests(BaseTest):
    def test_register_verify_build(self):
        """AC-01 / Figure 2A."""
        response = self.client.post("/auth/register", {
            "full_name": "Efua Owusu", "email": "efua@example.com", "phone": "024 412 3456",
            "password": "Card-maker-2026", "accept_terms": "on",
        })
        self.assertRedirects(response, "/auth/verify", fetch_redirect_response=False)
        user = User.objects.get(email="efua@example.com")
        self.assertEqual(user.phone, "+233244123456")
        self.assertEqual(user.cards.get().slug, "efua-owusu")
        # Dashboard is blocked until the email is confirmed.
        self.assertRedirects(self.client.get("/dashboard/"), "/auth/verify", fetch_redirect_response=False)
        link = re.search(r"http://[^\s]+/auth/verify/[^\s]+", mail.outbox[0].body).group(0)
        self.client.get(link.replace("http://127.0.0.1:8000", ""))
        user.refresh_from_db()
        self.assertTrue(user.is_email_verified)
        self.assertEqual(self.client.get("/dashboard/card/").status_code, 200)

    @override_settings(GOOGLE_CLIENT_ID="test-client", GOOGLE_CLIENT_SECRET="test-secret")
    def test_register_page_offers_google(self):
        response = self.client.get("/auth/register")
        self.assertContains(response, "Sign up with Google")
        self.assertContains(response, 'href="/auth/google"')

    def test_register_page_hides_google_without_keys(self):
        self.assertNotContains(self.client.get("/auth/register"), "Sign up with Google")

    @override_settings(GOOGLE_CLIENT_ID="test-client", GOOGLE_CLIENT_SECRET="test-secret")
    def test_sign_up_with_google(self):
        """A new Google user gets an account, a card and a welcome email, then adds a phone."""
        start = self.client.get("/auth/google")
        self.assertIn("accounts.google.com", start["Location"])
        state = self.client.session["google_state"]
        profile = {"sub": "google-123", "email": "abena@example.com", "name": "Abena Mensah"}
        with mock.patch("apps.accounts.services.google_user_from_code", return_value=(profile, None)):
            response = self.client.get(f"/auth/google/callback?state={state}&code=abc")
        self.assertRedirects(response, "/dashboard/settings/", fetch_redirect_response=False)
        user = User.objects.get(email="abena@example.com")
        self.assertEqual(user.google_sub, "google-123")
        self.assertTrue(user.is_email_verified)
        self.assertIsNotNone(user.accepted_terms_at)
        self.assertEqual(user.cards.get().slug, "abena-mensah")
        self.assertTrue(any("Welcome" in m.subject for m in mail.outbox))
        # Signing in with Google again finds the same account.
        self.client.logout()
        self.client.get("/auth/google")
        state = self.client.session["google_state"]
        with mock.patch("apps.accounts.services.google_user_from_code", return_value=(profile, None)):
            self.client.get(f"/auth/google/callback?state={state}&code=def")
        self.assertEqual(User.objects.filter(email="abena@example.com").count(), 1)

    def test_login_rate_limit(self):
        """SEC-04."""
        self.make_user()
        for _ in range(5):
            self.client.post("/auth/login", {"email": "kwame@example.com", "password": "wrong"})
        response = self.client.post("/auth/login", {"email": "kwame@example.com", "password": "Str0ng-pass-123"})
        self.assertEqual(response.status_code, 429)

    def test_password_reset_code(self):
        """REG-06, REG-07, REG-08, Figure 3B."""
        self.make_user()
        self.client.post("/auth/forgot-password", {"email": "kwame@example.com"})
        code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
        otp = OneTimeCode.objects.get()
        self.assertLess(otp.expires_at, timezone.now() + timedelta(minutes=16))
        # Four wrong tries, then the right one still works.
        for _ in range(4):
            self.client.post("/auth/forgot-password/verify", {"code": "000000", "password": "Another-pass-99", "password_confirm": "Another-pass-99"})
        response = self.client.post("/auth/forgot-password/verify", {"code": code, "password": "Another-pass-99", "password_confirm": "Another-pass-99"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.get().check_password("Another-pass-99"))
        self.assertTrue(any("password was changed" in m.subject for m in mail.outbox))

    def test_code_blocked_after_five_attempts(self):
        self.make_user()
        self.client.post("/auth/forgot-password", {"email": "kwame@example.com"})
        code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
        for _ in range(5):
            self.client.post("/auth/forgot-password/verify", {"code": "000000", "password": "Another-pass-99", "password_confirm": "Another-pass-99"})
        self.client.post("/auth/forgot-password/verify", {"code": code, "password": "Another-pass-99", "password_confirm": "Another-pass-99"})
        self.assertFalse(User.objects.get().check_password("Another-pass-99"))


class PaymentFlowTests(BaseTest):
    def setUp(self):
        self.user = self.make_user()
        self.card = self.make_card(self.user)
        self.client.force_login(self.user)

    def test_checkout_activates_card_once(self):
        """AC-07, AC-08 in sandbox mode."""
        response = self.client.post("/billing/", {"plan": "professional", "action": "pay", "accept_policy": "1"})
        self.assertEqual(response.status_code, 302)
        payment = Payment.objects.get()
        self.assertIn(f"reference={payment.reference}", response["Location"])
        with self.captureOnCommitCallbacks(execute=True):
            self.client.get(f"/billing/callback?reference={payment.reference}")
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.SUCCESS)
        self.assertTrue(payment.receipt_number.startswith("ADR-"))
        sub = Subscription.objects.get(user=self.user)
        first_expiry = sub.expires_at
        self.card.refresh_from_db()
        self.assertTrue(self.card.is_live)
        # Replaying the same reference must not add another year.
        self.client.get(f"/billing/callback?reference={payment.reference}")
        billing.verify_and_fulfil(payment, "replay")
        sub.refresh_from_db()
        self.assertEqual(sub.expires_at, first_expiry)
        self.assertTrue(any(m.attachments for m in mail.outbox))  # PDF receipt
        pdf = self.client.get(f"/billing/receipts/{payment.reference}.pdf")
        self.assertEqual(pdf["Content-Type"], "application/pdf")

    def test_refund_policy_must_be_accepted(self):
        self.client.post("/billing/", {"plan": "basic", "action": "pay"})
        self.assertFalse(Payment.objects.exists())

    def test_amount_mismatch_is_flagged_not_fulfilled(self):
        payment = billing.create_payment(
            user=self.user, purpose=Payment.PURPOSE_SUBSCRIPTION, quote=billing.subscription_quote(self.user, self.basic),
            plan=self.basic, months=12,
        )
        with mock.patch("apps.billing.paystack.verify", return_value={
            "status": "success", "reference": payment.reference, "amount": 1, "currency": "GHS"}):
            billing.verify_and_fulfil(payment, "test")
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.PENDING)
        self.assertIn("amount", payment.flag)
        self.assertFalse(Subscription.objects.filter(user=self.user).exists())

    def test_promo_code(self):
        from apps.billing.models import PromoCode

        PromoCode.objects.create(code="LAUNCH50", kind="percent", value=50, applies_to="subscription")
        self.client.post("/billing/", {"plan": "basic", "promo": "launch50", "action": "pay", "accept_policy": "1"})
        payment = Payment.objects.get()
        self.assertEqual(payment.amount_minor, 5000)
        self.client.get(f"/billing/callback?reference={payment.reference}")
        self.assertEqual(PromoCode.objects.get().used_count, 1)


@override_settings(PAYMENT_SANDBOX=False, PAYSTACK_SECRET_KEY="sk_test_secret")
class WebhookTests(BaseTest):
    def setUp(self):
        self.user = self.make_user()
        self.make_card(self.user)
        self.payment = billing.create_payment(
            user=self.user, purpose=Payment.PURPOSE_SUBSCRIPTION, quote=billing.subscription_quote(self.user, self.pro),
            plan=self.pro, months=12,
        )

    def post(self, body, signature):
        return self.client.post("/billing/webhook", data=body, content_type="application/json",
                                HTTP_X_PAYSTACK_SIGNATURE=signature)

    def test_bad_signature_rejected(self):
        """PAY-06."""
        body = json.dumps({"event": "charge.success", "data": {"reference": self.payment.reference}})
        self.assertEqual(self.post(body, "nope").status_code, 401)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.PENDING)

    def test_signed_webhook_is_reverified(self):
        """PAY-05: trust only after the Verify API agrees."""
        body = json.dumps({"event": "charge.success", "data": {"reference": self.payment.reference, "amount": 1}})
        signature = hmac.new(b"sk_test_secret", body.encode(), hashlib.sha512).hexdigest()
        verified = {"status": "success", "reference": self.payment.reference, "amount": self.payment.amount_minor,
                    "currency": "GHS", "channel": "mobile_money", "fees": 225}
        with mock.patch("apps.billing.paystack.verify", return_value=verified) as verify:
            self.assertEqual(self.post(body, signature).status_code, 200)
            self.assertEqual(self.post(body, signature).status_code, 200)  # Paystack retries
        verify.assert_called()
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.SUCCESS)
        self.assertEqual(self.payment.gateway_fee_minor, 225)
        self.assertEqual(AuditLog.objects.filter(action="payment_verified").count(), 1)


class NFCFlowTests(BaseTest):
    def setUp(self):
        self.user = self.make_user()
        self.card = self.make_card(self.user)
        self.client.force_login(self.user)
        self.zone = DeliveryZone.objects.filter(is_pickup=False).first()

    def order_payload(self, **extra):
        payload = {
            f"qty_{self.card.pk}": "4", "full_name": "Kwame Mensah", "email": "k@example.com", "phone": "0244000000",
            "design_description": "Navy front, logo left", "delivery_zone": self.zone.pk,
            "recipient_name": "Kwame", "recipient_phone": "0244000000", "delivery_address": "12 Castle Rd, Cape Coast",
            "plan": self.basic.pk, "accept_policy": "on",
        }
        payload.update(extra)
        return payload

    def test_order_with_bundled_plan_through_delivery(self):
        """NFL-03, AC-13. Emails are sent after commit, so run the flow inside
        captureOnCommitCallbacks and check them afterwards."""
        with self.captureOnCommitCallbacks(execute=True):
            order = self._order_through_delivery()
        self.assertEqual(order.status, NFCOrder.DELIVERED)
        self.assertIn("?src=nfc", order.items.get().encode_url)
        statuses = [e.kind for e in EmailLog.objects.filter(user=self.user)]
        self.assertIn("nfc:nfc_status_update", statuses)
        self.assertIn("nfc:nfc_proof_ready", statuses)

    def _order_through_delivery(self):
        response = self.client.post("/nfc/order", self.order_payload())
        self.assertEqual(response.status_code, 302, getattr(response, "context", {}) and response.context["form"].errors)
        order = NFCOrder.objects.get()
        self.assertEqual(order.cards_minor, 80000)
        self.assertEqual(order.total_minor, 80000 + self.zone.fee_minor + self.basic.price_minor)
        payment = order.payments.get()
        self.client.get(f"/nfc/callback?reference={payment.reference}")
        order.refresh_from_db()
        self.assertEqual(order.status, NFCOrder.PAYMENT_CONFIRMED)
        self.assertTrue(Subscription.objects.filter(user=self.user, plan=self.basic).exists())
        self.card.refresh_from_db()
        self.assertTrue(self.card.is_live)

        staff = User.objects.create_user(email="design@example.com", password="x", is_staff=True, staff_role="design")
        nfc.change_status(order, NFCOrder.DESIGN_IN_PROGRESS, staff)
        from django.core.files.base import ContentFile

        proof = nfc.add_proof(order, ContentFile(b"%PDF-1.4 test", name="nfc/proofs/p1.pdf"), staff)
        nfc.request_changes(order, proof, self.user, "Bigger logo")
        nfc.change_status(order, NFCOrder.DESIGN_IN_PROGRESS, staff)
        proof2 = nfc.add_proof(order, ContentFile(b"%PDF-1.4 test2", name="nfc/proofs/p2.pdf"), staff)
        response = self.client.post(f"/nfc/orders/{order.order_number}/proofs/{proof2.version}/approve")
        order.refresh_from_db()
        self.assertEqual(order.status, NFCOrder.APPROVED)
        nfc.change_status(order, NFCOrder.PRINTING, staff)
        nfc.change_status(order, NFCOrder.ENCODING_QC, staff)
        # QC must be recorded before the order can move on (NFC-05, NFC-06).
        with self.assertRaises(nfc.TransitionError):
            nfc.change_status(order, NFCOrder.READY_FOR_DELIVERY, staff)
        NFCOrder.objects.filter(pk=order.pk).update(qc_android_ok=True, qc_iphone_ok=True, tags_locked=True)
        nfc.change_status(order, NFCOrder.READY_FOR_DELIVERY, staff)
        with self.assertRaises(nfc.TransitionError):
            nfc.change_status(order, NFCOrder.DISPATCHED, staff)  # courier missing
        NFCOrder.objects.filter(pk=order.pk).update(courier_name="VIP Bus parcel")
        nfc.change_status(order, NFCOrder.DISPATCHED, staff)
        nfc.change_status(order, NFCOrder.DELIVERED, staff)
        order.refresh_from_db()
        return order

    def test_international_order(self):
        """Shipping outside Ghana: the customer pays the region's shipping fee."""
        self.subscribe(self.user)
        abroad = DeliveryZone.objects.filter(is_international=True, name="UK and Europe").get()
        payload = self.order_payload(plan="", delivery_zone=abroad.pk, delivery_address="221B Baker Street",
                                     recipient_phone="+44 7700 900123")
        # Country and city are required for shipping abroad.
        response = self.client.post("/nfc/order", payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn("delivery_country", response.context["form"].errors)
        self.assertIn("delivery_city", response.context["form"].errors)
        response = self.client.post("/nfc/order", dict(payload, delivery_country="GB", delivery_city="London", delivery_postcode="NW1 6XE"))
        self.assertEqual(response.status_code, 302)
        order = NFCOrder.objects.get()
        self.assertTrue(order.is_international)
        self.assertEqual(order.delivery_fee_minor, abroad.fee_minor)
        self.assertEqual(order.total_minor, 80000 + abroad.fee_minor)
        self.assertEqual(order.recipient_phone, "+447700900123")
        self.assertEqual(order.shipping_address_lines[-1], "United Kingdom")
        self.assertIn("Shipping and delivery", order.payments.get().line_items[1]["label"])

    def test_ghana_delivery_ignores_foreign_country(self):
        self.subscribe(self.user)
        self.client.post("/nfc/order", self.order_payload(plan="", delivery_country="GB", delivery_postcode="X1"))
        order = NFCOrder.objects.get()
        self.assertEqual(order.delivery_country.code, "GH")
        self.assertEqual(order.delivery_postcode, "")

    def test_cancellation_refund_rules(self):
        """RFD-01..03."""
        self.subscribe(self.user)
        self.client.post("/nfc/order", self.order_payload(plan=""))
        order = NFCOrder.objects.get()
        self.client.get(f"/nfc/callback?reference={order.payments.get().reference}")
        order.refresh_from_db()
        self.assertEqual(order.refundable_minor, order.total_minor)  # before design starts
        order.status = NFCOrder.DESIGN_IN_PROGRESS
        order.save()
        self.assertEqual(order.refundable_minor, order.cards_minor // 2)
        order.status = NFCOrder.PRINTING
        order.save()
        self.assertEqual(order.refundable_minor, 0)
        self.assertFalse(order.customer_can_cancel)


class StaffAccessTests(BaseTest):
    def login_staff(self, role):
        user = User.objects.create_user(email=f"{role}@example.com", password="Staff-pass-2026", is_staff=True, staff_role=role)
        secret = pyotp.random_base32()
        user.totp_secret = secret
        user.totp_confirmed_at = timezone.now()
        user.save()
        self.client.post("/staff/login", {"email": user.email, "password": "Staff-pass-2026"})
        self.client.post("/staff/2fa", {"code": pyotp.TOTP(secret).now()})
        return user

    def test_customers_get_404(self):
        self.client.force_login(self.make_user())
        self.assertEqual(self.client.get("/staff/").status_code, 404)
        self.assertEqual(self.client.get("/staff/db/").status_code, 404)

    def test_two_factor_required(self):
        """SEC-03."""
        user = User.objects.create_user(email="ops@example.com", password="Staff-pass-2026", is_staff=True, staff_role="ops")
        self.client.post("/staff/login", {"email": user.email, "password": "Staff-pass-2026"})
        self.assertRedirects(self.client.get("/staff/subscribers/"), "/staff/2fa?next=/staff/subscribers/", fetch_redirect_response=False)

    def test_role_matrix(self):
        """AC-14."""
        self.login_staff("design")
        self.assertEqual(self.client.get("/staff/nfc/").status_code, 200)
        self.assertEqual(self.client.get("/staff/payments/").status_code, 403)
        self.assertEqual(self.client.get("/staff/settings/").status_code, 403)
        self.client.post("/staff/logout")
        self.login_staff("finance")
        self.assertEqual(self.client.get("/staff/payments/").status_code, 200)
        self.assertEqual(self.client.get("/staff/payments/export.xlsx").status_code, 200)
        self.assertEqual(self.client.get("/staff/audit/").status_code, 403)

    def test_idle_timeout(self):
        """SEC-05."""
        self.login_staff("super")
        session = self.client.session
        session["staff_seen"] = session["staff_seen"] - 31 * 60
        session.save()
        response = self.client.get("/staff/subscribers/")
        self.assertTrue(response["Location"].startswith("/staff/login"))

    def test_manual_activation_is_audited(self):
        """ADM-01."""
        self.login_staff("support")
        customer = self.make_user("c@example.com")
        self.make_card(customer)
        self.client.post(f"/staff/subscribers/{customer.pk}/activate/", {
            "plan": self.pro.pk, "months": 12, "amount": "150.00", "reason": "Paid cash at the office",
        })
        self.assertTrue(Subscription.objects.filter(user=customer, plan=self.pro).exists())
        self.assertTrue(AuditLog.objects.filter(action="manual_activation", reason="Paid cash at the office").exists())


class DailyJobTests(BaseTest):
    def test_reminders_sent_once(self):
        """EXP-01, AC-11."""
        user = self.make_user()
        self.make_card(user)
        self.subscribe(user, days=6)
        call_command("run_daily_jobs", "--only", "reminders", verbosity=0)
        call_command("run_daily_jobs", "--only", "reminders", verbosity=0)
        self.assertEqual(EmailLog.objects.filter(kind="renewal_7").count(), 1)

    def test_deletion_after_30_days(self):
        """PRV-04."""
        user = self.make_user()
        card = self.make_card(user)
        user.deletion_requested_at = timezone.now() - timedelta(days=31)
        user.save()
        call_command("run_daily_jobs", "--only", "deletions", verbosity=0)
        user.refresh_from_db()
        card.refresh_from_db()
        self.assertIsNotNone(user.anonymised_at)
        self.assertTrue(user.email.endswith("@deleted.invalid"))
        self.assertEqual(self.client.get(f"/c/{card.slug}").status_code, 410)
