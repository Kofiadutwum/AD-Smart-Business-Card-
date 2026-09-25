"""Business rules from SRS v2.2: pricing, slugs, card states, plans, vCards."""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.hashers import WerkzeugPasswordHasher
from apps.billing import fx
from apps.billing.models import ExchangeRate, Subscription
from apps.billing.services import activate, upgrade_price
from apps.cards import services as card_services
from apps.cards.colors import contrast, palette
from apps.cards.models import CardSlug
from apps.nfc import services as nfc

from .helpers import BaseTest


class NFCPricingTests(BaseTest):
    def test_srs_examples(self):
        """AC-12: totals for 1, 4, 5, 9, 10, 20, 21 and 50 cards."""
        expected = {1: 20000, 4: 80000, 5: 80000, 9: 144000, 10: 150000, 20: 300000, 21: 313500, 30: 435000, 50: 705000}
        for quantity, total in expected.items():
            self.assertEqual(nfc.cards_price(quantity), total, quantity)

    def test_never_cheaper_for_more(self):
        self.assertEqual(nfc.pricing_problems(up_to=200), [])

    def test_add_one_more_hint_only_at_four(self):
        """NFP-02."""
        hinted = [q for q in range(1, 60) if nfc.free_extra_hint(q)]
        self.assertEqual(hinted, [4])


class SlugTests(BaseTest):
    def test_generated_from_name_and_deduplicated(self):
        user = self.make_user()
        first = self.make_card(user, "Kwame Mensah")
        other = self.make_user("k2@example.com")
        second = self.make_card(other, "Kwame Mensah")
        self.assertEqual(first.slug, "kwame-mensah")
        self.assertEqual(second.slug, "kwame-mensah-2")

    def test_rules(self):
        self.assertIsNotNone(card_services.slug_problem("ab"))
        self.assertIsNotNone(card_services.slug_problem("admin"))
        self.assertIsNotNone(card_services.slug_problem("Has-Caps"))
        self.assertIsNone(card_services.slug_problem("ama-serwaa"))

    def test_old_slug_redirects_and_is_never_reused(self):
        """URL-05, URL-06, AC-03."""
        user = self.make_user()
        card = self.make_card(user)
        self.subscribe(user)
        card_services.change_slug(card, "kwame-m")
        response = self.client.get("/c/kwame-mensah?src=qr")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/c/kwame-m?src=qr")
        other = self.make_card(self.make_user("x@example.com"), "Other Person")
        with self.assertRaises(ValidationError):
            card_services.change_slug(other, "kwame-mensah")

    def test_deleted_card_slug_retired(self):
        user = self.make_user()
        card = self.make_card(user)
        card_services.retire_card(card)
        self.assertEqual(self.client.get("/c/kwame-mensah").status_code, 410)
        self.assertTrue(card_services.slug_taken("kwame-mensah"))
        self.assertIsNotNone(CardSlug.objects.get(slug="kwame-mensah").retired_at)

    def test_slug_locked_after_nfc_order(self):
        """URL-04."""
        from apps.nfc.models import NFCOrder, NFCOrderItem

        user = self.make_user()
        card = self.make_card(user)
        order = NFCOrder.objects.create(order_number="NFC-TEST", user=user, quantity=1, cards_minor=20000,
                                        total_minor=20000, full_name="K", email="k@example.com", phone="+233244000000")
        NFCOrderItem.objects.create(order=order, card=card, quantity=1)
        with self.assertRaises(ValidationError):
            card_services.change_slug(card, "new-link")


class CardStateTests(BaseTest):
    def test_draft_page(self):
        """ACT-01, ACT-02."""
        user = self.make_user()
        self.make_card(user)
        response = self.client.get("/c/kwame-mensah")
        self.assertContains(response, "not yet active")
        self.assertNotContains(response, "Kwame")

    def test_live_grace_inactive(self):
        user = self.make_user()
        card = self.make_card(user, whatsapp="+233244000000")
        sub = self.subscribe(user)
        self.assertContains(self.client.get("/c/kwame-mensah"), "Kwame Mensah")
        sub.expires_at = timezone.now() - timedelta(days=3)  # inside the 14-day grace
        sub.save()
        self.assertEqual(sub.state, Subscription.STATE_GRACE)
        self.assertContains(self.client.get("/c/kwame-mensah"), "Kwame Mensah")
        sub.expires_at = timezone.now() - timedelta(days=20)
        sub.save()
        response = self.client.get("/c/kwame-mensah")
        self.assertContains(response, "currently inactive")
        self.assertNotContains(response, "Kwame")  # EXP-05
        card.refresh_from_db()
        self.assertEqual(card.slug, "kwame-mensah")  # EXP-07

    def test_suspended(self):
        user = self.make_user()
        card = self.make_card(user)
        self.subscribe(user)
        card.is_suspended = True
        card.save()
        self.assertContains(self.client.get("/c/kwame-mensah"), "unavailable")

    def test_missing(self):
        self.assertEqual(self.client.get("/c/nobody-here").status_code, 404)


class AnalyticsTests(BaseTest):
    def setUp(self):
        self.user = self.make_user()
        self.card = self.make_card(self.user)
        self.subscribe(self.user)

    def test_sources_including_flask_style(self):
        ua = {"HTTP_USER_AGENT": "Mozilla/5.0 (iPhone) Safari"}
        self.client.get("/c/kwame-mensah?src=qr", **ua)
        self.client.get("/c/kwame-mensah?s=nfc", **ua)  # tags written by the Flask site
        self.client.get("/c/kwame-mensah", **ua)
        sources = list(self.card.events.order_by("id").values_list("source", flat=True))
        self.assertEqual(sources, ["qr", "nfc", "link"])

    def test_bots_and_owner_not_counted(self):
        """ANL-02, ANL-03."""
        self.client.get("/c/kwame-mensah", HTTP_USER_AGENT="WhatsApp/2.23 A")
        self.client.get("/c/kwame-mensah", HTTP_USER_AGENT="facebookexternalhit/1.1")
        self.client.force_login(self.user)
        self.client.get("/c/kwame-mensah", HTTP_USER_AGENT="Mozilla/5.0")
        self.assertEqual(self.card.events.count(), 0)


class SubscriptionTests(BaseTest):
    def test_early_renewal_adds_to_expiry(self):
        """PLN-03."""
        user = self.make_user()
        sub = self.subscribe(user, days=100)
        before = sub.expires_at
        from django.db import transaction

        with transaction.atomic():
            activate(user, self.basic, 12)
        sub.refresh_from_db()
        self.assertGreater((sub.expires_at - before).days, 360)

    def test_downgrade_waits(self):
        """PLN-02."""
        user = self.make_user()
        sub = self.subscribe(user, plan=self.pro, days=100)
        from django.db import transaction

        with transaction.atomic():
            activate(user, self.basic, 12)
        sub.refresh_from_db()
        self.assertEqual(sub.plan, self.pro)
        self.assertEqual(sub.pending_plan, self.basic)

    def test_upgrade_pro_rated(self):
        """PLN-01: difference x full months remaining / 12."""
        user = self.make_user()
        sub = self.subscribe(user, plan=self.basic, days=190)
        amount, months = upgrade_price(sub, self.pro)
        self.assertEqual(months, 6)
        self.assertEqual(amount, (15000 - 10000) * 6 // 12)

    def test_plan_features_enforced_on_render(self):
        """AC-10: a Basic card cannot show a logo or a custom colour."""
        user = self.make_user()
        card = self.make_card(user)
        self.subscribe(user, plan=self.basic)
        card.accent = "#123456"
        card.logo = "logos/x.webp"
        card.save()
        eff = card.effective()
        self.assertIsNone(eff["logo"])
        self.assertNotEqual(eff["accent"], "#123456")
        self.assertEqual(eff["template"], "classic")


class ExchangeRateTests(BaseTest):
    def test_usd_hidden_after_72_hours(self):
        """CUR-04, AC-09."""
        ExchangeRate.objects.create(ghs_per_usd=Decimal("11.5"), provider="test", fetched_at=timezone.now() - timedelta(hours=80))
        from django.core.cache import cache

        cache.delete("fx:latest")
        self.assertIsNone(fx.latest_rate())
        response = self.client.get("/pricing")
        self.assertNotContains(response, "Approximate. You will be charged")
        ExchangeRate.objects.create(ghs_per_usd=Decimal("11.5"), provider="test")
        cache.delete("fx:latest")
        self.assertContains(self.client.get("/pricing"), "Approximate. You will be charged GHS 100.00")


class VCardTests(BaseTest):
    def test_vcard(self):
        """SHR-06: labelled numbers, CRLF line endings, card URL."""
        from apps.cards.models import CardPhone

        user = self.make_user()
        card = self.make_card(user, email="k@example.com", business_name="Adinkra, Ltd")
        CardPhone.objects.create(card=card, number="+233244000001", label="MTN")
        CardPhone.objects.create(card=card, number="+233204000002", label="Telecel")
        self.subscribe(user)
        response = self.client.get("/c/kwame-mensah/card.vcf", HTTP_USER_AGENT="Mozilla/5.0")
        body = response.content.decode()
        self.assertIn("BEGIN:VCARD\r\nVERSION:3.0", body)
        self.assertIn("TEL;TYPE=MTN,VOICE:+233244000001", body)
        self.assertIn("TEL;TYPE=TELECEL,VOICE:+233204000002", body)
        self.assertIn("ORG:Adinkra\\, Ltd", body)
        self.assertEqual(card.events.filter(kind="save_contact").count(), 1)


class ColourTests(BaseTest):
    def test_any_colour_gets_readable_text(self):
        """Section 12 / NFR-06 for custom colours."""
        for colour in ("#ffff00", "#00ff99", "#0048a9", "#222222", "#ff66cc"):
            p = palette(colour)
            self.assertGreaterEqual(contrast(p["on_accent"], colour), 4.5)
            self.assertGreaterEqual(contrast(p["accent_text_light"], "#ffffff"), 4.5)
            self.assertGreaterEqual(contrast(p["accent_text_dark"], "#15161a"), 4.5)


class HasherTests(BaseTest):
    def test_werkzeug_pbkdf2_and_scrypt(self):
        import hashlib

        hasher = WerkzeugPasswordHasher()
        salt = "abc123salt"
        pb = hashlib.pbkdf2_hmac("sha256", b"secret-pass", salt.encode(), 1000).hex()
        self.assertTrue(hasher.verify("secret-pass", f"werkzeug$pbkdf2:sha256:1000${salt}${pb}"))
        sc = hashlib.scrypt(b"secret-pass", salt=salt.encode(), n=1024, r=8, p=1, maxmem=132 * 1024 * 8, dklen=64).hex()
        self.assertTrue(hasher.verify("secret-pass", f"werkzeug$scrypt:1024:8:1${salt}${sc}"))
        self.assertFalse(hasher.verify("wrong", f"werkzeug$scrypt:1024:8:1${salt}${sc}"))
