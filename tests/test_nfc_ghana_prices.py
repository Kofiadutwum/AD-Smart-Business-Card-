"""NFC card prices for visitors in Ghana: their own table, shown under each plan and charged at the order."""

from apps.accounts.models import User
from apps.nfc import services as nfc
from apps.nfc.models import DeliveryZone, NFCOrder, PriceTier

from .helpers import BaseTest
from .test_ghana_pricing import ABROAD_IP, GHANA_IP


class GhanaTableTests(BaseTest):
    def test_ghana_table_totals(self):
        """The first 10 cards GHS 150 each, cards 11-20 GHS 130 each, cards above 20 GHS 115 each."""
        expected = {1: 15000, 5: 75000, 10: 150000, 11: 163000, 20: 280000, 21: 291500, 30: 395000, 50: 625000}
        for quantity, total in expected.items():
            self.assertEqual(nfc.cards_price(quantity, nfc.tiers(ghana=True)), total, quantity)

    def test_a_bigger_order_never_costs_less_in_ghana(self):
        self.assertEqual(nfc.pricing_problems(nfc.tiers(ghana=True), up_to=200), [])

    def test_everyone_else_keeps_the_common_table(self):
        self.assertEqual(nfc.cards_price(1, nfc.tiers()), 20000)
        self.assertEqual(nfc.cards_price(5, nfc.tiers()), 80000)
        self.assertEqual(nfc.pricing_problems(nfc.tiers(), up_to=200), [])

    def test_without_a_ghana_table_ghana_pays_the_common_one(self):
        PriceTier.objects.filter(ghana=True).delete()
        self.assertEqual(nfc.tiers(ghana=True), nfc.tiers())


class PlanCardTests(BaseTest):
    def test_each_plan_shows_the_card_price_in_ghana_only(self):
        for page in ("/", "/pricing"):
            ghana = self.client.get(page, REMOTE_ADDR=GHANA_IP)
            self.assertContains(ghana, "Printed card: GHS 150.00", count=3, msg_prefix=page)  # Basic, Professional, Business
            self.assertNotContains(self.client.get(page, REMOTE_ADDR=ABROAD_IP), "Printed card:", msg_prefix=page)


class OrderTests(BaseTest):
    def setUp(self):
        self.user = self.make_user()
        self.card = self.make_card(self.user)
        self.client.force_login(self.user)
        self.zone = DeliveryZone.objects.filter(is_pickup=False).first()

    def place(self, quantity, ip):
        payload = {
            f"qty_{self.card.pk}": str(quantity), "full_name": "Kwame Mensah", "email": "k@example.com",
            "phone": "0244000000", "design_description": "Navy front", "delivery_zone": self.zone.pk,
            "recipient_name": "Kwame", "recipient_phone": "0244000000", "delivery_address": "12 Castle Rd, Cape Coast",
            "plan": self.basic.pk, "accept_policy": "on",
        }
        response = self.client.post("/nfc/order", payload, REMOTE_ADDR=ip)
        self.assertEqual(response.status_code, 302, getattr(response, "context", {}) and response.context["form"].errors)
        return NFCOrder.objects.latest("pk")

    def test_the_order_is_priced_from_the_visitors_table(self):
        self.assertEqual(self.place(11, GHANA_IP).cards_minor, 163000)
        self.assertEqual(self.place(11, ABROAD_IP).cards_minor, 11 * 15000)  # the common table: 10 to 20 cards cost GHS 150 each

    def test_the_order_page_calculator_gets_the_visitors_table(self):
        ghana = self.client.get("/nfc/order", REMOTE_ADDR=GHANA_IP).context["tiers_json"]
        abroad = self.client.get("/nfc/order", REMOTE_ADDR=ABROAD_IP).context["tiers_json"]
        self.assertEqual(ghana[0]["unit"], 15000)
        self.assertEqual(abroad[0]["unit"], 20000)


class StaffPriceScreenTests(BaseTest):
    URL = "/staff/settings/pricing/"

    def setUp(self):
        User.objects.create_user(email="boss@example.com", password="Staff-pass-2026", is_staff=True, staff_role="super")
        self.client.post("/staff/login", {"email": "boss@example.com", "password": "Staff-pass-2026"})

    def payload(self, **changes):
        rows = list(PriceTier.objects.all())
        data = {"save": "tiers", "tiers-TOTAL_FORMS": len(rows) + 1, "tiers-INITIAL_FORMS": len(rows),
                "tiers-MIN_NUM_FORMS": 0, "tiers-MAX_NUM_FORMS": 1000,
                f"tiers-{len(rows)}-mode": PriceTier.FLAT}  # the blank extra row, left as the browser sends it
        for i, tier in enumerate(rows):
            data.update({
                f"tiers-{i}-id": tier.pk, f"tiers-{i}-min_quantity": tier.min_quantity,
                f"tiers-{i}-max_quantity": tier.max_quantity or "", f"tiers-{i}-mode": tier.mode,
                f"tiers-{i}-unit_price": f"{changes.get(tier.pk, tier.unit_price_minor) / 100:.2f}",
            })
            if tier.ghana:
                data[f"tiers-{i}-ghana"] = "on"
        return data

    def test_page_shows_both_tables(self):
        page = self.client.get(self.URL)
        self.assertContains(page, "Price table for visitors in Ghana")
        self.assertContains(page, "Visitors in Ghana")

    def test_ghana_table_saves_unchanged(self):
        response = self.client.post(self.URL, self.payload())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PriceTier.objects.filter(ghana=True).count(), 3)

    def refuses(self, tier, price):
        """Posting ``price`` (cedis) for ``tier`` is refused and nothing is saved."""
        bad = self.payload()
        bad[f"tiers-{list(PriceTier.objects.all()).index(tier)}-unit_price"] = price
        self.assertEqual(self.client.post(self.URL, bad).status_code, 200)
        before = tier.unit_price_minor
        tier.refresh_from_db()
        self.assertEqual(tier.unit_price_minor, before)

    def test_common_table_still_refuses_a_cheaper_larger_order(self):
        self.refuses(PriceTier.objects.get(ghana=False, min_quantity=5), "50.00")  # 9 cards would cost less than 4

    def test_ghana_table_refuses_a_gap(self):
        first_band = PriceTier.objects.get(ghana=True, min_quantity=1)
        bad = self.payload()
        bad[f"tiers-{list(PriceTier.objects.all()).index(first_band)}-max_quantity"] = "8"  # 9 and 10 cards would have no price
        self.assertEqual(self.client.post(self.URL, bad).status_code, 200)
        first_band.refresh_from_db()
        self.assertEqual(first_band.max_quantity, 10)
