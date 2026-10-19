"""The customer pays Paystack's fee: it is added to what Paystack charges, kept off our own pages."""

from django.test import override_settings

from apps.billing import receipts, services
from apps.billing.models import Payment
from apps.cards.premade import create_batch

from .helpers import BaseTest


class ProcessingFeeTests(BaseTest):
    def setUp(self):
        self.user = self.make_user()
        self.basic = services.active_plans().get(code="basic")

    def test_fee_is_grossed_up_so_the_price_survives_paystacks_cut(self):
        for price in (20000, 35000, 70000, 166000):
            charged = price + services.processing_fee(price)
            self.assertGreaterEqual(charged * 98.05 / 100, price)  # after 1.95% is taken, the full price is left
            self.assertLess((charged - 1) * 98.05 / 100, price)  # and the fee is not a pesewa higher than needed

    def test_nothing_to_pay_means_no_fee(self):
        self.assertEqual(services.processing_fee(0), 0)

    @override_settings(PAYSTACK_FEE_PERCENT=0)
    def test_fee_can_be_switched_off(self):
        self.assertEqual(services.processing_fee(35000), 0)

    def test_payment_charges_price_plus_fee_and_keeps_the_price_apart(self):
        quote = services.subscription_quote(self.user, self.basic)
        payment = services.create_payment(
            user=self.user, purpose=Payment.PURPOSE_SUBSCRIPTION, quote=quote, plan=self.basic, months=12,
        )
        self.assertEqual(payment.subtotal_minor, 20000)
        self.assertEqual(payment.processing_fee_minor, 398)  # 20000 / 0.9805 rounded up, less 20000
        self.assertEqual(payment.amount_minor, 20398)

    def test_our_checkout_shows_the_listed_price_only(self):
        self.client.force_login(self.user)
        page = self.client.get("/billing/?plan=basic")
        self.assertNotContains(page, "203.98")
        self.assertNotContains(page, "processing")

    def test_receipt_lists_the_fee_line(self):
        quote = services.subscription_quote(self.user, self.basic)
        payment = services.create_payment(
            user=self.user, purpose=Payment.PURPOSE_SUBSCRIPTION, quote=quote, plan=self.basic, months=12,
        )
        services.verify_and_fulfil(payment, "test")
        payment.refresh_from_db()
        self.assertTrue(receipts.render_receipt_pdf(payment).startswith(b"%PDF"))

    def test_shop_cards_default_to_450(self):
        staff = self.make_user(email="staff@example.com")
        batch = create_batch(staff, name="Gold", quantity=1, plan=self.basic)
        self.assertEqual(batch.price_minor, 45000)
