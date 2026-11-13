"""Pay-when-you-update pre-made cards: live free, changes free for the first year, then paid (the business page never)."""

from datetime import timedelta

from dateutil.relativedelta import relativedelta
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import Payment
from apps.billing.services import card_update_quote, create_payment
from apps.cards import premade
from apps.cards.models import BusinessPage, Card, CardUpdate, PremadeCard
from apps.core.models import AuditLog
from apps.dashboard import updates

from .test_business import BusinessBase, picture
from .test_premade import BUYER

UPDATE_PRICE = 10000


def editor_defaults():
    return {
        "full_name": "Ama Boateng", "job_title": "Photographer", "business_name": "Ama Studio", "bio": "Hello",
        "whatsapp": "", "email": "ama@example.com", "website": "", "address": "", "ghana_post_gps": "",
        "template": "classic", "button_style": "pill", "icon_layout": "list", "accent_preset": "#0048A9",
        "visible": ["email", "phones", "whatsapp", "website", "address", "ghana_post_gps", "map", "bio"],
        "is_published": "on", "collect_leads": "on",
        "phones-TOTAL_FORMS": "1", "phones-INITIAL_FORMS": "0", "phones-MIN_NUM_FORMS": "0",
        "phones-MAX_NUM_FORMS": "5", "phones-0-number": "024 555 1212", "phones-0-label": "",
    }


class PayToUpdateBase(BusinessBase):
    """A bought-and-set-up card from a pay-when-you-update batch; files go to a throwaway folder."""

    def data(self, **over):
        """The editor's post, editing the card's existing phone number the way the real page does."""
        data = editor_defaults()
        phone = self.card.phones.first()
        if phone:
            data.update({"phones-INITIAL_FORMS": "1", "phones-0-id": str(phone.pk)})
        data.update(over)
        return data

    def make_batch(self, plan=None, quantity=2, update_price=UPDATE_PRICE):
        staff, _made = User.objects.get_or_create(
            email="ops@example.com", defaults={"password": "x", "is_staff": True, "staff_role": "ops"},
        )
        batch = premade.create_batch(
            staff, name="Pay Later", quantity=quantity, plan=plan or self.pro, price_minor=20000,
            pay_to_update=True, update_price_minor=update_price,
        )
        return batch, premade.get_pack(batch)

    def set_up(self, plan=None, free_year=False):
        """Buy and set up a card. It is made 13 months ago, so its free first year is over, unless ``free_year``."""
        batch, rows = self.make_batch(plan)
        code = rows[0]["code"]
        self.client.post(f"/c/{code}/claim", {"pin": rows[0]["pin"]})
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f"/c/{code}/claim/setup", BUYER)
        self.user = User.objects.get(email="ama@example.com")
        if not free_year:
            self.made(code, months=13)
        self.card = Card.objects.get(slug=code)
        return batch

    def made(self, slug, months):
        """Pretend the card was made ``months`` ago (created_at is set by the database on creation)."""
        Card.objects.filter(slug=slug).update(created_at=timezone.now() - relativedelta(months=months))

    def pay(self, card=None, action="pay"):
        """Press 'Pay and unlock changes' (or the small Mobile Money button) and come back from the sandbox page."""
        card = card or self.card
        response = self.client.post(f"/dashboard/cards/{card.pk}/unlock/", {"accept_policy": "1", "action": action})
        self.assertEqual(response.status_code, 302, response.content[:300])
        with self.captureOnCommitCallbacks(execute=True):
            self.client.get(response["Location"])
        card.refresh_from_db()
        return Payment.objects.filter(purpose=Payment.PURPOSE_CARD_UPDATE).latest("pk")


class BatchRulesTests(PayToUpdateBase):
    def test_basic_and_professional_are_allowed_and_business_is_not(self):
        for plan in (self.basic, self.pro):
            batch, _rows = self.make_batch(plan, quantity=1)
            self.assertTrue(batch.pay_to_update)
            self.assertEqual((batch.months, batch.update_price_minor, batch.card_only), (0, UPDATE_PRICE, False))
        with self.assertRaises(ValidationError):
            self.make_batch(self.business, quantity=1)

    def test_staff_form_asks_for_a_plan_and_the_update_price(self):
        from apps.staff.forms import PremadeBatchForm

        base = {"name": "Pay Later", "quantity": 5, "term": "payupdate", "plan": self.pro.pk}
        form = PremadeBatchForm(base)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual((form.cleaned_data["price"], form.cleaned_data["update_price"]), (200, 100))
        form = PremadeBatchForm({**base, "price": "250", "update_price": "80"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual((form.cleaned_data["price"], form.cleaned_data["update_price"]), (250, 80))
        self.assertFalse(PremadeBatchForm({**base, "plan": self.business.pk}).is_valid())


class SetUpTests(PayToUpdateBase):
    def test_setup_is_free_and_the_card_is_live_at_once_with_no_plan(self):
        batch = self.set_up()
        self.assertIsNone(self.user.subscription)
        self.card.refresh_from_db()
        self.assertEqual(self.card.update_plan, self.pro)
        self.assertEqual(self.card.update_price_minor, UPDATE_PRICE)
        self.assertTrue(self.card.is_live)
        self.assertTrue(self.card.edit_locked)
        self.assertEqual(PremadeCard.objects.get(card=self.card).status, "claimed")
        self.client.logout()
        page = self.client.get(f"/c/{self.card.slug}")
        self.assertContains(page, "Ama Boateng")
        sale = Payment.objects.get(user=self.user)
        self.assertEqual((sale.purpose, sale.amount_minor, sale.gateway), (Payment.PURPOSE_PREMADE, 20000, "shop"))
        self.assertNotIn("0 months", sale.description)
        self.assertIn("pay when you update", sale.description)
        self.assertEqual(batch.cards.filter(claimed_at__isnull=False).count(), 1)

    def test_a_buyer_with_an_account_and_a_plan_keeps_the_plan_and_the_card_uses_no_place(self):
        batch, rows = self.make_batch()
        owner = self.make_user(email="old@example.com")
        own_card = self.make_card(owner)
        self.subscribe(owner, self.basic)
        own_card.refresh_from_db()
        self.client.force_login(owner)
        code = rows[0]["code"]
        self.client.post(f"/c/{code}/claim", {"pin": rows[0]["pin"]})
        self.client.post(f"/c/{code}/claim/attach")
        card = Card.objects.get(slug=code)
        self.assertEqual(card.owner_id, owner.pk)
        self.assertTrue(card.is_live)
        self.assertFalse(card.edit_locked)  # the free first year
        owner.subscription.refresh_from_db()
        self.assertEqual(owner.subscription.plan, self.basic)  # nothing was added to the plan
        own_card.refresh_from_db()
        self.assertTrue(own_card.is_enabled)  # still has its place

    def test_a_running_plan_does_not_unlock_the_card(self):
        self.set_up()
        self.subscribe(self.user, self.business)
        self.card.refresh_from_db()
        self.assertTrue(self.card.edit_locked)
        self.assertTrue(self.card.is_live)

    def test_a_plan_bought_later_does_not_touch_the_card(self):
        self.set_up()
        before = self.card.activated_at
        self.subscribe(self.user, self.basic)
        self.card.refresh_from_db()
        self.assertEqual((self.card.activated_at, self.card.update_plan), (before, self.pro))


class LockedEditorTests(PayToUpdateBase):
    def test_the_editor_says_what_a_change_costs(self):
        self.set_up()
        page = self.client.get(f"/dashboard/cards/{self.card.pk}/edit/")
        self.assertContains(page, "Changing your details, links or web address costs")
        self.assertContains(page, "Your business page is always free")

    def test_saving_holds_the_changes_and_sends_the_owner_to_pay(self):
        self.set_up()
        response = self.client.post(f"/dashboard/cards/{self.card.pk}/edit/", self.data(full_name="Ama B. Boateng"))
        self.assertRedirects(response, f"/dashboard/cards/{self.card.pk}/unlock/", fetch_redirect_response=False)
        self.card.refresh_from_db()
        self.assertEqual(self.card.full_name, "Ama Boateng")  # nothing changed on the card yet
        held = CardUpdate.objects.get(card=self.card)
        self.assertEqual(held.data["full_name"], ["Ama B. Boateng"])
        page = self.client.get(f"/dashboard/cards/{self.card.pk}/unlock/")
        self.assertContains(page, "Your changes are saved and waiting")
        self.assertContains(self.client.get(f"/dashboard/cards/{self.card.pk}/edit/"), "changes waiting for payment")

    def test_a_form_with_mistakes_is_not_held(self):
        self.set_up()
        response = self.client.post(f"/dashboard/cards/{self.card.pk}/edit/", self.data(full_name=""))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CardUpdate.objects.exists())

    def test_a_new_save_replaces_the_one_that_was_waiting(self):
        self.set_up()
        url = f"/dashboard/cards/{self.card.pk}/edit/"
        self.client.post(url, self.data(full_name="First Try"))
        self.client.post(url, self.data(full_name="Second Try"))
        self.assertEqual(CardUpdate.objects.get(card=self.card).data["full_name"], ["Second Try"])

    def test_the_owner_can_throw_the_waiting_changes_away(self):
        self.set_up()
        self.client.post(f"/dashboard/cards/{self.card.pk}/edit/", self.data(full_name="Nope", avatar_upload=picture()))
        stored = [info["path"] for info in CardUpdate.objects.get().files.values()]
        self.assertEqual(len(stored), 1)
        self.client.post(f"/dashboard/cards/{self.card.pk}/discard-changes/")
        self.assertFalse(CardUpdate.objects.exists())
        self.assertFalse(default_storage.exists(stored[0]))


class PayingTests(PayToUpdateBase):
    def test_the_unlock_page_shows_the_update_price_and_needs_the_policy_ticked(self):
        self.set_up()
        url = f"/dashboard/cards/{self.card.pk}/unlock/"
        self.assertContains(self.client.get(url), "Unlock changes to your card")
        response = self.client.post(url, {})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Payment.objects.filter(purpose=Payment.PURPOSE_CARD_UPDATE).exists())

    def test_paying_unlocks_a_year_and_puts_the_waiting_changes_on_the_card(self):
        self.set_up()
        self.client.post(
            f"/dashboard/cards/{self.card.pk}/edit/",
            self.data(full_name="Ama B. Boateng", job_title="Videographer", **{"phones-0-number": "020 111 2222"}),
        )
        payment = self.pay()
        self.assertEqual((payment.status, payment.card_id, payment.months), (Payment.SUCCESS, self.card.pk, 12))
        self.assertEqual(payment.subtotal_minor, UPDATE_PRICE)
        self.assertEqual(payment.line_items[0]["label"], "Card updates for 12 months")
        self.assertEqual(self.card.full_name, "Ama B. Boateng")
        self.assertEqual(self.card.job_title, "Videographer")
        self.assertEqual(self.card.phones.get().number, "+233201112222")
        self.assertFalse(CardUpdate.objects.exists())
        self.assertFalse(self.card.edit_locked)
        months = (self.card.updates_paid_until - timezone.now()).days
        self.assertIn(months, (364, 365, 366))
        receipt = [m for m in mail.outbox if payment.receipt_number in m.subject]
        self.assertEqual(len(receipt), 1)
        self.assertIn("Changes to your card are open until", receipt[0].body)

    def test_a_picture_chosen_with_the_changes_is_held_and_applied_after_payment(self):
        self.set_up()
        self.client.post(f"/dashboard/cards/{self.card.pk}/edit/", self.data(avatar_upload=picture()))
        held = CardUpdate.objects.get()
        self.assertIn("avatar_upload", held.files)
        self.card.refresh_from_db()
        self.assertFalse(self.card.avatar)  # not on the card until paid
        self.pay()
        self.assertTrue(self.card.avatar)
        self.assertFalse(CardUpdate.objects.exists())
        self.assertFalse(default_storage.exists(held.files["avatar_upload"]["path"]))  # the held copy is gone

    def test_a_replayed_callback_cannot_grant_a_second_year(self):
        self.set_up()
        payment = self.pay()
        until = self.card.updates_paid_until
        self.client.get(f"/billing/callback?reference={payment.reference}")
        self.card.refresh_from_db()
        self.assertEqual(self.card.updates_paid_until, until)

    def test_when_the_card_is_open_the_editor_saves_straight_away(self):
        self.set_up()
        self.pay()
        response = self.client.post(f"/dashboard/cards/{self.card.pk}/edit/", self.data(job_title="Director"))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("unlock", response["Location"])
        self.card.refresh_from_db()
        self.assertEqual(self.card.job_title, "Director")
        self.assertFalse(CardUpdate.objects.exists())
        self.assertContains(self.client.get(f"/dashboard/cards/{self.card.pk}/edit/"), "are open until")
        self.assertRedirects(  # nothing to pay while it is open
            self.client.get(f"/dashboard/cards/{self.card.pk}/unlock/"), f"/dashboard/cards/{self.card.pk}/edit/",
            fetch_redirect_response=False,
        )

    def test_after_a_year_the_card_keeps_working_and_the_next_change_needs_another_payment(self):
        self.set_up()
        self.pay()
        Card.objects.filter(pk=self.card.pk).update(updates_paid_until=timezone.now() - timedelta(days=1))
        self.card.refresh_from_db()
        self.assertTrue(self.card.edit_locked)
        self.assertTrue(self.card.is_live)
        self.assertEqual(self.client.get(f"/c/{self.card.slug}").status_code, 200)
        response = self.client.post(f"/dashboard/cards/{self.card.pk}/edit/", self.data(job_title="Late"))
        self.assertRedirects(response, f"/dashboard/cards/{self.card.pk}/unlock/", fetch_redirect_response=False)
        self.pay()
        self.assertEqual(self.card.job_title, "Late")
        self.assertGreater(self.card.updates_paid_until, timezone.now() + timedelta(days=360))

    def test_changes_that_no_longer_pass_are_dropped_but_the_year_still_starts(self):
        self.set_up()
        self.client.post(f"/dashboard/cards/{self.card.pk}/edit/", self.data(full_name="Fine"))
        CardUpdate.objects.update(data={"full_name": [""]})  # something that can never validate
        self.pay()
        self.assertEqual(self.card.full_name, "Ama Boateng")
        self.assertFalse(self.card.edit_locked)
        self.assertFalse(CardUpdate.objects.exists())


class SmallScreenTests(PayToUpdateBase):
    """Links and the card web address do not hold changes: after the free year they ask to pay first. The business page never does."""

    def unlock_url(self):
        return f"/dashboard/cards/{self.card.pk}/unlock/"

    def test_links_ask_to_pay_first(self):
        self.set_up()
        self.assertRedirects(self.client.get("/dashboard/links/"), self.unlock_url(), fetch_redirect_response=False)
        response = self.client.post("/dashboard/links/", {"platform": "instagram", "url": "https://instagram.com/ama"})
        self.assertRedirects(response, self.unlock_url(), fetch_redirect_response=False)
        self.assertFalse(self.card.social_links.exists())

    def test_removing_or_moving_a_link_asks_to_pay_first(self):
        self.set_up()
        link = self.card.social_links.create(platform="instagram", url="https://instagram.com/ama")
        self.assertRedirects(
            self.client.post(f"/dashboard/links/{link.pk}/delete/"), self.unlock_url(), fetch_redirect_response=False,
        )
        self.assertTrue(self.card.social_links.exists())

    def test_changing_the_card_web_address_asks_to_pay_first(self):
        self.set_up()
        old = self.card.slug
        response = self.client.post(f"/dashboard/cards/{self.card.pk}/slug/", {"slug": "ama-photos"})
        self.assertRedirects(response, self.unlock_url(), fetch_redirect_response=False)
        self.card.refresh_from_db()
        self.assertEqual(self.card.slug, old)

    def test_the_business_page_never_asks_to_pay(self):
        self.set_up()
        self.assertTrue(self.card.edit_locked)
        self.assertEqual(self.client.get("/dashboard/business/").status_code, 200)
        response = self.client.post("/dashboard/business/services/add/", {"title": "Shoots", "description": ""})
        self.assertRedirects(response, "/dashboard/business/", fetch_redirect_response=False)
        service = BusinessPage.objects.get(owner=self.user).services.get(title="Shoots")
        response = self.client.post(f"/dashboard/business/services/{service.pk}/edit/", {"title": "Weddings", "description": ""})
        self.assertRedirects(response, "/dashboard/business/", fetch_redirect_response=False)
        self.assertEqual(BusinessPage.objects.get(owner=self.user).services.get().title, "Weddings")
        response = self.client.post(f"/dashboard/business/services/{service.pk}/delete/")
        self.assertRedirects(response, "/dashboard/business/", fetch_redirect_response=False)
        self.assertFalse(BusinessPage.objects.get(owner=self.user).services.exists())
        self.assertFalse(CardUpdate.objects.exists())

    def test_once_paid_these_screens_work(self):
        self.set_up()
        self.pay()
        response = self.client.post("/dashboard/links/", {"platform": "instagram", "url": "https://instagram.com/ama"})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("unlock", response["Location"])
        self.assertEqual(self.card.social_links.count(), 1)
        self.client.post(f"/dashboard/cards/{self.card.pk}/slug/", {"slug": "ama-photos"})
        self.card.refresh_from_db()
        self.assertEqual(self.card.slug, "ama-photos")
        self.client.post("/dashboard/business/services/add/", {"title": "Shoots", "description": ""})
        self.assertTrue(BusinessPage.objects.get(owner=self.user).services.filter(title="Shoots").exists())

    def test_what_stays_free_stays_free(self):
        self.set_up()
        for url in ("/dashboard/", "/dashboard/share/", "/dashboard/analytics/", "/dashboard/contacts/", "/dashboard/settings/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        toggled = self.client.post(f"/dashboard/cards/{self.card.pk}/toggle/")
        self.assertEqual(toggled.status_code, 302)
        self.assertNotIn("unlock", toggled["Location"])
        self.card.refresh_from_db()
        self.assertFalse(self.card.is_enabled)


class KeptCardTests(PayToUpdateBase):
    """Paying by card keeps it; the first Save after the year asks for a confirm, then charges it."""

    def unlock_url(self):
        return f"/dashboard/cards/{self.card.pk}/unlock/"

    def year_over(self):
        Card.objects.filter(pk=self.card.pk).update(updates_paid_until=timezone.now() - timedelta(days=1))
        self.card.refresh_from_db()

    def edit_after_the_year(self, **over):
        """Paid by card, the year runs out, and the owner presses Save on a change."""
        self.set_up()
        self.pay()
        self.year_over()
        return self.client.post(f"/dashboard/cards/{self.card.pk}/edit/", self.data(**over))

    def card_payments(self):
        return Payment.objects.filter(purpose=Payment.PURPOSE_CARD_UPDATE)

    def test_paying_by_card_keeps_the_card_without_the_number(self):
        self.set_up()
        payment = self.pay()
        self.assertTrue(payment.set_up_auto_renew)
        self.assertTrue(self.card.pays_automatically)
        self.assertEqual(self.card.update_card_label, "Visa ending 4081")
        self.assertTrue(self.card.update_authorization_code.startswith("AUTH_"))
        self.assertIn("We kept your card (Visa ending 4081)", [m for m in mail.outbox if payment.receipt_number in m.subject][0].body)

    def test_paying_by_mobile_money_keeps_nothing(self):
        self.set_up()
        payment = self.pay(action="pay_momo")
        self.assertFalse(payment.set_up_auto_renew)
        self.assertFalse(self.card.pays_automatically)
        self.assertEqual(self.card.update_authorization_code, "")
        self.assertNotIn("We kept your card", [m for m in mail.outbox if payment.receipt_number in m.subject][0].body)

    def test_the_pay_page_offers_card_first_and_a_small_mobile_money_button(self):
        self.set_up()
        html = self.client.get(self.unlock_url()).content.decode()
        self.assertLess(html.index('value="pay"'), html.index('value="pay_momo"'))
        self.assertIn("Mobile Money is never kept", html)

    def test_the_first_save_after_the_year_asks_to_confirm_and_charges_nothing(self):
        response = self.edit_after_the_year(job_title="Late")
        self.assertRedirects(response, self.unlock_url(), fetch_redirect_response=False)
        self.assertEqual(self.card_payments().count(), 1)  # only the first year's payment
        page = self.client.get(self.unlock_url())
        self.assertContains(page, "Your card (Visa ending 4081) will be charged")
        self.assertContains(page, "Confirm and charge my card")
        self.assertContains(page, "Pay another way")
        self.card.refresh_from_db()
        self.assertNotEqual(self.card.job_title, "Late")  # still waiting
        self.assertTrue(CardUpdate.objects.exists())

    def test_confirming_charges_the_kept_card_and_puts_the_changes_on_the_card(self):
        self.edit_after_the_year(job_title="Late")
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.unlock_url(), {"action": "confirm"})
        payment = self.card_payments().latest("pk")
        self.assertRedirects(response, f"/billing/result/{payment.reference}", fetch_redirect_response=False)
        self.assertEqual(self.card_payments().count(), 2)
        self.assertEqual((payment.status, payment.card_id, payment.months), (Payment.SUCCESS, self.card.pk, 12))
        self.assertEqual(payment.subtotal_minor, UPDATE_PRICE)
        self.card.refresh_from_db()
        self.assertEqual(self.card.job_title, "Late")
        self.assertFalse(CardUpdate.objects.exists())
        self.assertFalse(self.card.edit_locked)
        self.assertGreater(self.card.updates_paid_until, timezone.now() + timedelta(days=360))
        self.assertTrue(self.card.pays_automatically)  # the card stays for the year after

    def test_a_declined_charge_keeps_the_changes_and_offers_another_way(self):
        self.edit_after_the_year(job_title="Late")
        with override_settings(PAYMENT_SANDBOX_OUTCOME="failed"):
            response = self.client.post(self.unlock_url(), {"action": "confirm"})
        self.assertRedirects(response, f"{self.unlock_url()}?other=1", fetch_redirect_response=False)
        self.assertEqual(self.card_payments().latest("pk").status, Payment.FAILED)
        self.card.refresh_from_db()
        self.assertTrue(self.card.edit_locked)
        self.assertTrue(self.card.pays_automatically)  # one bad day does not forget the card
        self.assertTrue(CardUpdate.objects.exists())
        page = self.client.get(f"{self.unlock_url()}?other=1")
        self.assertContains(page, "was declined")
        self.assertContains(page, "Pay and unlock changes")
        self.assertNotContains(page, "Confirm and charge my card")

    def test_pay_another_way_shows_the_usual_page_and_paying_there_replaces_the_card(self):
        self.edit_after_the_year(job_title="Late")
        self.assertContains(self.client.get(f"{self.unlock_url()}?other=1"), "Pay and unlock changes")
        Card.objects.filter(pk=self.card.pk).update(update_authorization_code="AUTH_old", update_card_last4="1111")
        self.pay()
        self.assertTrue(self.card.update_authorization_code.startswith("AUTH_sandbox"))
        self.assertEqual(self.card.update_card_last4, "4081")
        self.assertEqual(self.card.job_title, "Late")

    def test_a_kept_card_past_its_expiry_goes_straight_to_the_usual_page(self):
        self.edit_after_the_year()
        Card.objects.filter(pk=self.card.pk).update(update_card_expiry="01/2020")
        page = self.client.get(self.unlock_url())
        self.assertContains(page, "Pay and unlock changes")
        self.assertNotContains(page, "Confirm and charge my card")
        self.client.post(self.unlock_url(), {"action": "confirm"})  # a stray confirm charges nothing
        self.assertEqual(self.card_payments().count(), 1)

    def test_a_second_click_does_not_charge_twice(self):
        self.edit_after_the_year()
        busy = create_payment(
            user=self.user, purpose=Payment.PURPOSE_CARD_UPDATE, quote=card_update_quote(self.card), months=12, card=self.card,
        )
        Payment.objects.filter(pk=busy.pk).update(channel="saved-card")  # a charge still waiting on Paystack
        with override_settings(PAYMENT_SANDBOX_OUTCOME="abandoned"):  # Paystack has not settled it yet
            response = self.client.post(self.unlock_url(), {"action": "confirm"})
        self.assertRedirects(response, f"/billing/result/{busy.reference}", fetch_redirect_response=False)
        self.assertEqual(self.card_payments().count(), 2)

    def test_small_screens_use_the_confirm_step_too(self):
        self.set_up()
        self.pay()
        self.year_over()
        response = self.client.get("/dashboard/links/")
        self.assertRedirects(response, self.unlock_url(), fetch_redirect_response=False)
        self.assertContains(self.client.get(self.unlock_url()), "Confirm and charge my card")

    def test_the_billing_page_shows_it_and_turns_it_off(self):
        self.set_up()
        self.pay()
        page = self.client.get("/billing/")
        self.assertContains(page, "Automatic payment for changes to Ama Boateng is on")
        self.assertContains(page, "Visa ending 4081")
        mail.outbox.clear()
        response = self.client.post(f"/billing/update-card/{self.card.pk}/stop")
        self.assertRedirects(response, "/billing/", fetch_redirect_response=False)
        self.card.refresh_from_db()
        self.assertFalse(self.card.pays_automatically)
        self.assertEqual((self.card.update_authorization_code, self.card.update_card_last4), ("", ""))
        self.assertNotContains(self.client.get("/billing/"), "Automatic payment for changes to Ama Boateng")
        self.assertEqual([m.subject for m in mail.outbox], ["Your card will no longer be charged for changes"])
        self.assertTrue(AuditLog.objects.filter(action="update_autopay_stopped").exists())
        self.year_over()
        self.assertContains(self.client.get(self.unlock_url()), "Pay and unlock changes")  # back to paying by hand

    def test_deleting_the_account_forgets_the_kept_card(self):
        from apps.accounts.services import anonymise_user

        self.set_up()
        self.pay()
        anonymise_user(self.user, reason="test")
        self.card.refresh_from_db()
        self.assertFalse(self.card.pays_automatically)
        self.assertEqual((self.card.update_card_last4, self.card.update_authorization_email), ("", ""))

    def test_nobody_else_can_turn_it_off(self):
        self.set_up()
        self.pay()
        self.client.logout()
        self.client.force_login(self.make_user("other@example.com"))
        self.assertEqual(self.client.post(f"/billing/update-card/{self.card.pk}/stop").status_code, 404)
        self.card.refresh_from_db()
        self.assertTrue(self.card.pays_automatically)


class PlanFeatureTests(PayToUpdateBase):
    def test_a_basic_card_has_the_basic_features_whatever_the_owner_buys_elsewhere(self):
        self.set_up(self.basic)
        self.subscribe(self.user, self.business)
        self.card.refresh_from_db()
        eff = self.card.effective()
        self.assertEqual(eff["plan"], self.basic)
        self.assertEqual(eff["template"], "classic")

    def test_a_basic_card_has_no_business_page(self):
        self.set_up(self.basic)
        self.assertContains(self.client.get("/dashboard/business/"), "Business pages come with Professional")


class CleanupTests(PayToUpdateBase):
    def test_unpaid_held_changes_are_thrown_away_after_48_hours_with_their_pictures(self):
        self.set_up()
        self.client.post(f"/dashboard/cards/{self.card.pk}/edit/", self.data(avatar_upload=picture()))
        path = next(iter(CardUpdate.objects.get().files.values()))["path"]
        call_command("run_daily_jobs", only="held_updates", verbosity=0)
        self.assertEqual(CardUpdate.objects.count(), 1)  # still fresh
        CardUpdate.objects.update(created_at=timezone.now() - timedelta(hours=49))
        call_command("run_daily_jobs", only="held_updates", verbosity=0)
        self.assertFalse(CardUpdate.objects.exists())
        self.assertFalse(default_storage.exists(path))

    def test_changes_held_on_a_card_that_is_open_again_are_applied_not_thrown_away(self):
        self.set_up()
        self.client.post(f"/dashboard/cards/{self.card.pk}/edit/", self.data(job_title="Held"))
        self.assertTrue(CardUpdate.objects.exists())
        self.made(self.card.slug, months=1)  # the rule changed under it: the free year is running
        CardUpdate.objects.update(created_at=timezone.now() - timedelta(hours=49))  # even past the 48 hours
        call_command("run_daily_jobs", only="held_updates", verbosity=0)
        self.card.refresh_from_db()
        self.assertEqual(self.card.job_title, "Held")
        self.assertFalse(CardUpdate.objects.exists())
        self.assertTrue(AuditLog.objects.filter(action="card_update_applied").exists())


class FreeYearTests(PayToUpdateBase):
    """Details, links and web address are free for a year from the day the card is made; the business page always."""

    def test_a_new_card_is_open_and_saves_straight_away(self):
        self.set_up(free_year=True)
        self.assertFalse(self.card.edit_locked)
        self.assertTrue(self.card.is_live)
        self.assertContains(self.client.get(f"/dashboard/cards/{self.card.pk}/edit/"), "are open until")
        response = self.client.post(f"/dashboard/cards/{self.card.pk}/edit/", self.data(job_title="Director"))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("unlock", response["Location"])
        self.card.refresh_from_db()
        self.assertEqual(self.card.job_title, "Director")
        self.assertFalse(CardUpdate.objects.exists())

    def test_links_and_the_web_address_are_free_in_the_first_year(self):
        self.set_up(free_year=True)
        response = self.client.post("/dashboard/links/", {"platform": "instagram", "url": "https://instagram.com/ama"})
        self.assertNotIn("unlock", response["Location"])
        self.assertEqual(self.card.social_links.count(), 1)
        self.client.post(f"/dashboard/cards/{self.card.pk}/slug/", {"slug": "ama-photos"})
        self.card.refresh_from_db()
        self.assertEqual(self.card.slug, "ama-photos")
        self.assertRedirects(  # nothing to pay while it is open
            self.client.get(f"/dashboard/cards/{self.card.pk}/unlock/"), f"/dashboard/cards/{self.card.pk}/edit/",
            fetch_redirect_response=False,
        )

    def test_the_free_year_counts_from_the_day_the_card_was_made(self):
        self.set_up(free_year=True)
        for months, locked in ((0, False), (11, False), (13, True), (24, True)):
            self.made(self.card.slug, months)
            self.card.refresh_from_db()
            self.assertEqual(self.card.edit_locked, locked, months)
        self.assertEqual(self.card.updates_free_until, self.card.created_at + relativedelta(months=12))

    def test_a_year_that_was_paid_for_still_counts_after_the_free_year(self):
        self.set_up()  # made 13 months ago
        Card.objects.filter(pk=self.card.pk).update(updates_paid_until=timezone.now() + timedelta(days=60))
        self.card.refresh_from_db()
        self.assertFalse(self.card.edit_locked)
        self.assertEqual(self.card.updates_open_until, self.card.updates_paid_until)
        Card.objects.filter(pk=self.card.pk).update(updates_paid_until=timezone.now() - timedelta(days=1))
        self.card.refresh_from_db()
        self.assertTrue(self.card.edit_locked)

    def test_a_year_paid_during_the_free_year_starts_when_the_free_year_ends(self):
        self.set_up(free_year=True)
        payment = create_payment(
            user=self.user, purpose=Payment.PURPOSE_CARD_UPDATE, quote=card_update_quote(self.card), months=12, card=self.card,
        )
        updates.unlock_and_apply(payment)
        self.card.refresh_from_db()
        self.assertEqual(self.card.updates_paid_until, self.card.updates_free_until + relativedelta(months=12))

    def test_the_business_page_is_free_in_the_first_year(self):  # after it: SmallScreenTests
        self.set_up(free_year=True)
        response = self.client.post("/dashboard/business/services/add/", {"title": "Shoots", "description": ""})
        self.assertRedirects(response, "/dashboard/business/", fetch_redirect_response=False)
        self.assertTrue(BusinessPage.objects.get(owner=self.user).services.filter(title="Shoots").exists())

    def test_the_claim_page_says_the_first_year_is_free(self):
        _batch, rows = self.make_batch()
        code = rows[0]["code"]
        self.client.post(f"/c/{code}/claim", {"pin": rows[0]["pin"]})
        page = self.client.get(f"/c/{code}/claim/setup")
        self.assertContains(page, "free for your first year")
        self.assertContains(page, "business page is always free")

    def test_the_dashboard_says_until_when_changes_are_open(self):
        self.set_up(free_year=True)
        self.assertContains(self.client.get("/dashboard/"), "Changes are open until")


@override_settings(STAFF_TWO_FACTOR_REQUIRED=False)
class StaffViewTests(PayToUpdateBase):
    def test_the_staff_list_names_the_new_kind_of_batch(self):
        batch, _rows = self.make_batch()
        User.objects.create_user(email="boss@example.com", password="Staff-pass-2026", is_staff=True, staff_role="super")
        self.client.post("/staff/login", {"email": "boss@example.com", "password": "Staff-pass-2026"})
        page = self.client.get("/staff/premade-cards/")
        self.assertContains(page, "Pay when updating")
        self.assertContains(page, "Update Price")
        detail = self.client.get(f"/staff/premade-cards/{batch.pk}/")
        self.assertContains(detail, "pay when updating")
        self.assertContains(detail, "Update Price")

    def staff_in(self):
        User.objects.create_user(email="boss@example.com", password="Staff-pass-2026", is_staff=True, staff_role="super")
        self.client.post("/staff/login", {"email": "boss@example.com", "password": "Staff-pass-2026"})

    def test_an_owner_of_a_pay_when_updating_card_is_live_not_a_draft(self):
        self.set_up(free_year=True)
        self.staff_in()
        row = self.client.get("/staff/subscribers/?q=ama@example.com")
        self.assertContains(row, "Live · pay when updating")
        self.assertContains(row, "changes open until")
        self.assertNotContains(row, '<span class="badge">Draft</span>')
        self.assertContains(self.client.get("/staff/subscribers/?state=update"), "ama@example.com")
        self.assertNotContains(self.client.get("/staff/subscribers/?state=draft"), "ama@example.com")
        detail = self.client.get(f"/staff/subscribers/{self.user.pk}/")
        self.assertContains(detail, "Pay when updating, Professional features")
        self.assertContains(detail, "Free first year until")
        self.assertNotContains(detail, "No plan yet (draft)")

    def test_the_staff_home_does_not_count_that_owner_as_inactive_or_draft(self):
        self.set_up(free_year=True)
        self.staff_in()
        others = User.objects.filter(is_staff=False, subscription_record__isnull=True).exclude(pk=self.user.pk).count()
        self.assertEqual(self.client.get("/staff/").context["inactive"], others)
