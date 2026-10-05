"""Pre-made cards: sold in shops, set up by the buyer with the PIN on the back."""

import io
import zipfile
from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import Payment, Subscription
from apps.cards import premade
from apps.cards.models import Card, PremadeCard

from .helpers import BaseTest

BUYER = {
    "full_name": "Ama Boateng", "job_title": "Photographer", "business_name": "Ama Studio",
    "email": "ama@example.com", "phone": "024 555 1212", "password": "Camera-lens-2026", "accept_terms": "on",
}


class PremadeBase(BaseTest):
    def make_batch(self, quantity=3, plan=None, price=35000):
        staff = User.objects.create_user(email="ops@example.com", password="x", is_staff=True, staff_role="ops")
        batch = premade.create_batch(staff, name="Gold Classic", quantity=quantity, plan=plan or self.pro, price_minor=price)
        rows = premade.get_pack(batch)
        return batch, rows

    def card_for(self, row):
        return PremadeCard.objects.select_related("card", "batch").get(card__slug=row["code"])

    def verify_pin(self, row, client=None):
        client = client or self.client
        response = client.post(f"/c/{row['code']}/claim", {"pin": row["pin"]})
        self.assertEqual(response.status_code, 302, response.content[:300])
        return response


class BatchTests(PremadeBase):
    def test_batch_makes_unclaimed_cards_with_random_links_and_hashed_pins(self):
        batch, rows = self.make_batch(quantity=5)
        self.assertEqual(batch.cards.count(), 5)
        self.assertEqual(len(rows), 5)
        slugs = {r["code"] for r in rows}
        self.assertEqual(len(slugs), 5)
        for row in rows:
            self.assertEqual(len(row["code"]), 8)
            self.assertIn(f"/c/{row['code']}?src=qr", row["qr_url"])
            self.assertIn(f"/c/{row['code']}?src=nfc", row["nfc_url"])
            self.assertRegex(row["pin"], r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")
            pc = self.card_for(row)
            self.assertIsNone(pc.card.owner_id)
            self.assertNotIn(row["pin"].replace("-", ""), pc.pin_hash)
            self.assertEqual(pc.status, "unclaimed")
        self.assertEqual(batch.price_minor, 35000)

    def test_print_pack_has_links_pins_and_qr_images(self):
        batch, rows = self.make_batch(quantity=2)
        text = premade.pack_csv(rows).decode("utf-8-sig")
        self.assertIn("PIN for the back", text)
        self.assertIn(rows[0]["pin"], text)
        archive = zipfile.ZipFile(io.BytesIO(premade.pack_zip(rows)))
        names = archive.namelist()
        self.assertIn("cards.csv", names)
        self.assertEqual(len([n for n in names if n.startswith("svg/")]), 2)
        self.assertEqual(len([n for n in names if n.startswith("png/")]), 2)

    def test_pins_are_not_stored_readably_after_the_pack_expires(self):
        batch, rows = self.make_batch(quantity=1)
        cache.delete(premade.pack_key(batch))
        self.assertIsNone(premade.get_pack(batch))

    def test_reissue_replaces_pins(self):
        batch, rows = self.make_batch(quantity=2)
        staff = User.objects.get(email="ops@example.com")
        new_rows = premade.reissue_pins(batch, staff)
        old = {r["code"]: r["pin"] for r in rows}
        new = {r["code"]: r["pin"] for r in new_rows}
        self.assertEqual(set(old), set(new))
        self.assertTrue(all(old[c] != new[c] for c in old))
        code = rows[0]["code"]
        wrong = self.client.post(f"/c/{code}/claim", {"pin": old[code]})
        self.assertContains(wrong, "not right")
        self.verify_pin({**rows[0], "pin": new[code]})


class LinkTests(PremadeBase):
    def test_unclaimed_link_leads_to_setup_and_nothing_else(self):
        batch, rows = self.make_batch(quantity=1)
        code = rows[0]["code"]
        response = self.client.get(f"/c/{code}")
        self.assertRedirects(response, f"/c/{code}/claim", fetch_redirect_response=False)
        self.assertEqual(self.client.get(f"/c/{code}/card.vcf").status_code, 404)
        self.assertEqual(self.client.get(f"/c/{code}/qr.png").status_code, 404)
        self.assertContains(self.client.get(f"/c/{code}/claim"), "PIN")

    def test_setup_page_needs_the_pin_first(self):
        batch, rows = self.make_batch(quantity=1)
        code = rows[0]["code"]
        self.assertRedirects(self.client.get(f"/c/{code}/claim/setup"), f"/c/{code}/claim", fetch_redirect_response=False)

    def test_wrong_pin_counts_down_then_locks_the_card(self):
        batch, rows = self.make_batch(quantity=1)
        code = rows[0]["code"]
        first = self.client.post(f"/c/{code}/claim", {"pin": "AAAA-AAAA"})
        self.assertContains(first, "4 tries left")
        for _ in range(3):
            self.client.post(f"/c/{code}/claim", {"pin": "AAAA-AAAA"})
        locked = self.client.post(f"/c/{code}/claim", {"pin": "AAAA-AAAA"})
        self.assertContains(locked, "locked for 60 minutes")
        # Even the right PIN is refused while the card is locked.
        again = self.client.post(f"/c/{code}/claim", {"pin": rows[0]["pin"]})
        self.assertContains(again, "Too many wrong PINs")
        pc = self.card_for(rows[0])
        pc.pin_locked_until = timezone.now() - timedelta(minutes=1)
        pc.save()
        self.verify_pin(rows[0])

    def test_pin_is_accepted_however_it_is_typed(self):
        batch, rows = self.make_batch(quantity=1)
        code, pin = rows[0]["code"], rows[0]["pin"]
        typed = " " + pin.lower().replace("-", " ") + " "
        self.verify_pin({"code": code, "pin": typed})


class ClaimTests(PremadeBase):
    def test_new_buyer_sets_up_the_card_and_gets_a_year(self):
        batch, rows = self.make_batch(quantity=2)
        code = rows[0]["code"]
        self.verify_pin(rows[0])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(f"/c/{code}/claim/setup", BUYER)
        # Straight into the account: no "check your email" page in the way.
        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)

        user = User.objects.get(email="ama@example.com")
        self.assertFalse(user.is_email_verified)
        self.assertTrue(user.can_use_account)
        card = Card.objects.get(slug=code)
        self.assertEqual(card.owner_id, user.pk)
        self.assertEqual(card.full_name, "Ama Boateng")
        self.assertEqual(card.job_title, "Photographer")
        self.assertEqual(card.phones.get().number, "+233245551212")
        self.assertEqual(card.slug, code)  # the printed link never changes

        subscription = user.subscription
        self.assertEqual(subscription.plan, self.pro)
        days = (subscription.expires_at - timezone.now()).days
        self.assertIn(days, (364, 365, 366))

        payment = Payment.objects.get(user=user)
        self.assertEqual(payment.purpose, Payment.PURPOSE_PREMADE)
        self.assertEqual(payment.amount_minor, 35000)
        self.assertEqual(payment.status, Payment.SUCCESS)
        self.assertEqual(payment.gateway, "shop")
        self.assertTrue(payment.receipt_number.startswith("ADR-"))
        self.assertIn("Pre-made card", payment.description)

        self.assertTrue(any(payment.receipt_number in m.subject for m in mail.outbox))
        pc = PremadeCard.objects.get(card=card)
        self.assertEqual(pc.status, "claimed")
        self.assertEqual(pc.claimed_by_id, user.pk)

        # The same link now shows the buyer's card for everybody.
        self.client.logout()
        page = self.client.get(f"/c/{code}")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Ama Boateng")
        self.assertTrue(card.is_live)

    def test_claimed_card_cannot_be_claimed_again(self):
        batch, rows = self.make_batch(quantity=1)
        code = rows[0]["code"]
        self.verify_pin(rows[0])
        self.client.post(f"/c/{code}/claim/setup", BUYER)
        stranger = self.client.__class__()
        response = stranger.get(f"/c/{code}/claim")
        self.assertRedirects(response, f"/c/{code}", fetch_redirect_response=False)
        response = stranger.post(f"/c/{code}/claim", {"pin": rows[0]["pin"]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.filter(email="ama@example.com").count(), 1)

    def test_setup_checks_the_form(self):
        batch, rows = self.make_batch(quantity=1)
        code = rows[0]["code"]
        self.verify_pin(rows[0])
        bad = {**BUYER, "accept_terms": ""}
        response = self.client.post(f"/c/{code}/claim/setup", bad)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="ama@example.com").exists())
        self.assertTrue(Card.objects.get(slug=code).is_unclaimed)

    def test_revenue_shows_up_as_outside_website_sales(self):
        batch, rows = self.make_batch(quantity=1)
        self.verify_pin(rows[0])
        self.client.post(f"/c/{rows[0]['code']}/claim/setup", BUYER)
        boss = User.objects.create_user(email="boss@example.com", password="x", is_staff=True, is_superuser=True)
        self.client.logout()
        self.client.post("/staff/login", {"email": boss.email, "password": "x"})
        report = self.client.get("/staff/reports/finance/")
        self.assertContains(report, "Pre-made cards (outside the website)")
        self.assertEqual(report.context["totals"]["premade"], 35000)
        self.assertEqual(report.context["totals"]["subscription"], 0)
        home = self.client.get("/staff/")
        self.assertEqual(home.context["premade_revenue"], 35000)
        self.assertEqual(home.context["month_revenue"], 35000)
        payments = self.client.get("/staff/payments/?purpose=premade_card")
        self.assertContains(payments, "ama@example.com")


class StraightInTests(PremadeBase):
    """A shop-card buyer goes straight into the dashboard; confirming the email can wait."""

    def claim(self):
        batch, rows = self.make_batch(quantity=2)
        self.verify_pin(rows[0])
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f"/c/{rows[0]['code']}/claim/setup", BUYER)
        return User.objects.get(email="ama@example.com")

    def test_dashboard_opens_at_once_and_asks_for_the_email_to_be_confirmed(self):
        self.claim()
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm your email address")
        self.assertEqual(self.client.get("/dashboard/card/").status_code, 200)
        self.assertEqual(self.client.get("/billing/").status_code, 200)

    def test_the_confirmation_link_is_still_sent_and_works_later(self):
        import re

        user = self.claim()
        link = re.search(r"http://[^\s]+/auth/verify/[^\s]+", next(m for m in mail.outbox if "Confirm your email" in m.subject).body).group(0)
        self.client.get(link.replace("http://127.0.0.1:8000", ""))
        user.refresh_from_db()
        self.assertTrue(user.is_email_verified)
        self.assertNotContains(self.client.get("/dashboard/"), "Confirm your email address")

    def test_the_banner_can_send_the_link_again_without_leaving_the_dashboard(self):
        self.claim()
        before = len(mail.outbox)
        response = self.client.post("/auth/verify")
        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), before + 1)
        self.assertRedirects(self.client.get("/auth/verify"), "/dashboard/", fetch_redirect_response=False)

    def test_a_normal_sign_up_still_has_to_confirm_first(self):
        self.client.post("/auth/register", {
            "full_name": "Efua Owusu", "email": "efua@example.com", "phone": "024 412 3456",
            "password": "Card-maker-2026", "accept_terms": "on",
        })
        user = User.objects.get(email="efua@example.com")
        self.assertIsNone(user.verification_waived_at)
        self.assertFalse(user.can_use_account)
        self.assertRedirects(self.client.get("/dashboard/"), "/auth/verify", fetch_redirect_response=False)


class ExistingAccountTests(PremadeBase):
    def attach(self, user, row):
        self.client.force_login(user)
        self.verify_pin(row)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(f"/c/{row['code']}/claim/attach")

    def test_basic_subscriber_moves_to_professional_with_a_year_added(self):
        batch, rows = self.make_batch(quantity=1)
        user = self.make_user()
        subscription = self.subscribe(user, self.basic, days=100)
        before = subscription.expires_at
        response = self.attach(user, rows[0])
        self.assertRedirects(response, "/dashboard/cards/", fetch_redirect_response=False)
        subscription = Subscription.objects.get(user=user)
        self.assertEqual(subscription.plan, self.pro)
        self.assertGreater((subscription.expires_at - before).days, 360)
        card = Card.objects.get(slug=rows[0]["code"])
        self.assertEqual(card.owner_id, user.pk)
        self.assertTrue(card.is_live)

    def test_business_subscriber_keeps_the_plan_and_gets_the_year(self):
        batch, rows = self.make_batch(quantity=1)
        user = self.make_user()
        subscription = self.subscribe(user, self.business, days=100)
        before = subscription.expires_at
        self.attach(user, rows[0])
        subscription = Subscription.objects.get(user=user)
        self.assertEqual(subscription.plan, self.business)
        self.assertGreater((subscription.expires_at - before).days, 360)
        self.assertTrue(Card.objects.get(slug=rows[0]["code"]).is_live)

    def test_account_with_no_free_card_place_is_told_why(self):
        batch, rows = self.make_batch(quantity=1)
        user = self.make_user()
        self.subscribe(user, self.pro)
        self.make_card(user)  # Professional allows one live card
        self.client.force_login(user)
        self.verify_pin(rows[0])
        page = self.client.get(f"/c/{rows[0]['code']}/claim/attach")
        self.assertContains(page, "already uses all of its live card places")
        self.client.post(f"/c/{rows[0]['code']}/claim/attach")
        self.assertTrue(Card.objects.get(slug=rows[0]["code"]).is_unclaimed)
        self.assertFalse(Payment.objects.filter(purpose=Payment.PURPOSE_PREMADE).exists())

    def test_login_link_comes_back_to_the_claim(self):
        batch, rows = self.make_batch(quantity=1)
        code = rows[0]["code"]
        self.verify_pin(rows[0])
        page = self.client.get(f"/c/{code}/claim/setup")
        self.assertContains(page, f"/auth/login?next=/c/{code}/claim/attach")


class VoidTests(PremadeBase):
    def test_void_takes_a_waiting_card_out_of_use(self):
        batch, rows = self.make_batch(quantity=2)
        staff = User.objects.get(email="ops@example.com")
        pc = self.card_for(rows[0])
        premade.void_card(pc, staff, reason="Lost in print")
        code = rows[0]["code"]
        self.assertEqual(self.client.get(f"/c/{code}").status_code, 410)
        self.assertEqual(self.client.get(f"/c/{code}/claim").status_code, 410)
        other = rows[1]["code"]
        self.assertEqual(self.client.get(f"/c/{other}").status_code, 302)

    def test_a_set_up_card_cannot_be_voided(self):
        batch, rows = self.make_batch(quantity=1)
        self.verify_pin(rows[0])
        self.client.post(f"/c/{rows[0]['code']}/claim/setup", BUYER)
        staff = User.objects.get(email="ops@example.com")
        with self.assertRaises(Exception):
            premade.void_card(self.card_for(rows[0]), staff)


class StaffPageTests(PremadeBase):
    def login_staff(self, role):
        user = User.objects.create_user(email=f"{role}@example.com", password="Staff-pass-2026", is_staff=True, staff_role=role)
        self.client.post("/staff/login", {"email": user.email, "password": "Staff-pass-2026"})
        return user

    def test_ops_can_make_a_batch_and_download_the_pack(self):
        self.login_staff("ops")
        response = self.client.post("/staff/premade-cards/", {
            "name": "Gold Classic", "quantity": 4, "plan": self.pro.pk, "price": "350", "note": "First print",
        })
        self.assertEqual(response.status_code, 302)
        from apps.cards.models import PremadeBatch

        batch = PremadeBatch.objects.get()
        self.assertEqual(batch.quantity, 4)
        self.assertEqual(batch.price_minor, 35000)
        self.assertEqual(batch.months, 12)
        page = self.client.get(f"/staff/premade-cards/{batch.pk}/")
        self.assertContains(page, "QR codes (ZIP")
        csv_response = self.client.get(f"/staff/premade-cards/{batch.pk}/pack.csv")
        self.assertEqual(csv_response.status_code, 200)
        self.assertEqual(csv_response.content.decode("utf-8-sig").count("\n"), 5)
        zip_response = self.client.get(f"/staff/premade-cards/{batch.pk}/pack.zip")
        self.assertEqual(zip_response["Content-Type"], "application/zip")
        self.assertContains(self.client.get("/staff/premade-cards/"), "Gold Classic")

    def test_roles_without_access_are_turned_away(self):
        self.login_staff("support")
        self.assertEqual(self.client.get("/staff/premade-cards/").status_code, 403)
        self.client.post("/staff/logout")
        self.login_staff("finance")
        self.assertEqual(self.client.get("/staff/premade-cards/").status_code, 403)
