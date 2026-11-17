"""Written text on the public pages follows what a visitor in Ghana really pays: plans paid once, paid changes, their own NFC prices."""

from .helpers import BaseTest
from .test_ghana_pricing import ABROAD_IP, GHANA_IP


class FaqTests(BaseTest):
    def test_ghana_reads_about_a_plan_paid_once(self):
        page = self.client.get("/faq", REMOTE_ADDR=GHANA_IP)
        self.assertContains(page, "Do I have to renew?")
        self.assertContains(page, "No. You pay once and your plan never ends.")
        self.assertContains(page, "a payment of GHS 100.00 opens 12 more months of changes to that card")
        self.assertNotContains(page, "How does renewal work?")
        self.assertNotContains(page, "Does my plan renew automatically?")

    def test_everyone_else_still_reads_about_yearly_renewal(self):
        page = self.client.get("/faq", REMOTE_ADDR=ABROAD_IP)
        self.assertContains(page, "How does renewal work?")
        self.assertNotContains(page, "Do I have to renew?")
        self.assertNotContains(page, "12 more months of changes")


class HomeTests(BaseTest):
    def test_no_update_any_time_promise_in_ghana(self):
        ghana = self.client.get("/", REMOTE_ADDR=GHANA_IP)
        self.assertNotContains(ghana, "Update your details any time")
        self.assertNotContains(ghana, "Update it any time")
        abroad = self.client.get("/", REMOTE_ADDR=ABROAD_IP)
        self.assertContains(abroad, "Update your details any time")
        self.assertContains(abroad, "Update it any time")


class TermsTests(BaseTest):
    def test_terms_cover_both_kinds_of_plan(self):
        page = self.client.get("/terms")
        self.assertContains(page, "Plans paid once")
        self.assertContains(page, "Yearly plans")
        self.assertContains(page, "A plan paid once never ends and is never renewed.")
        self.assertNotContains(page, "Plans are paid yearly")


class RefundTests(BaseTest):
    def test_a_payment_for_more_months_of_changes_is_not_refundable(self):
        page = self.client.get("/refunds")
        self.assertContains(page, "A payment to open more months of changes to a card is not refundable once it is made")
        self.assertContains(page, "NFC card orders")  # the order refund schedule is still there


class DescriptionTests(BaseTest):
    def test_search_descriptions_quote_the_visitors_lowest_nfc_price(self):
        self.assertContains(self.client.get("/nfc-cards", REMOTE_ADDR=GHANA_IP), "From GHS 115.00 per card")
        self.assertContains(self.client.get("/nfc-cards", REMOTE_ADDR=ABROAD_IP), "From GHS 135.00 per card")
        self.assertContains(self.client.get("/pricing", REMOTE_ADDR=GHANA_IP), "NFC cards from GHS 115.00 each")
        self.assertContains(self.client.get("/pricing", REMOTE_ADDR=ABROAD_IP), "NFC cards from GHS 135.00 each")
