"""Ghana: plans paid once (found from the visitor's internet address), a free first year of changes, then a payment per card."""

from dateutil.relativedelta import relativedelta
from django.core import mail
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.utils import timezone

from apps.billing import services as billing
from apps.billing.models import PAID_ONCE_END, Payment, Subscription
from apps.cards.models import Card, CardUpdate
from apps.core import geo
from apps.nfc.views import _order_quote

from .helpers import BaseTest
from .test_pay_to_update import editor_defaults

GHANA_IP = "154.160.5.5"  # inside a Ghanaian range of the country database
ABROAD_IP = "8.8.8.8"


class CountryLookupTests(SimpleTestCase):
    def request(self, remote, forwarded=""):
        meta = {"REMOTE_ADDR": remote}
        if forwarded:
            meta["HTTP_X_FORWARDED_FOR"] = forwarded
        return RequestFactory().get("/", **meta)

    def test_addresses_map_to_countries(self):
        self.assertEqual(geo.country_of(GHANA_IP), "GH")
        self.assertEqual(geo.country_of(ABROAD_IP), "US")
        self.assertEqual(geo.country_of("2606:4700::1111"), "US")  # the database covers IPv6 too
        self.assertEqual(geo.country_of("::ffff:154.160.5.5"), "GH")  # an IPv4 address written the IPv6 way

    def test_unknown_addresses_are_nobody(self):
        for unknown in ("", "not an address", "127.0.0.1", "192.168.1.5", "10.0.0.2"):
            self.assertEqual(geo.country_of(unknown), "", unknown)

    def test_a_forged_forwarding_header_cannot_pick_the_country(self):
        self.assertFalse(geo.in_ghana(self.request(ABROAD_IP, forwarded=GHANA_IP)))

    def test_behind_a_local_proxy_the_forwarded_address_is_used(self):
        self.assertTrue(geo.in_ghana(self.request("127.0.0.1", forwarded=f"{GHANA_IP}, 10.0.0.1")))

    def test_the_country_can_be_forced_for_a_local_preview(self):
        with override_settings(GEO_FORCE_COUNTRY="gh"):
            self.assertTrue(geo.in_ghana(self.request(ABROAD_IP)))


class PricingPageTests(BaseTest):
    def test_ghana_sees_one_time_prices(self):
        page = self.client.get("/pricing", REMOTE_ADDR=GHANA_IP)
        self.assertContains(page, "Pay once. Keep your card.")
        self.assertContains(page, "paid once")
        self.assertNotContains(page, "No yearly renewal")
        self.assertNotContains(page, "free for 12 months")
        self.assertNotContains(page, "One simple yearly price.")

    def test_everyone_else_keeps_the_yearly_prices(self):
        page = self.client.get("/pricing", REMOTE_ADDR=ABROAD_IP)
        self.assertContains(page, "One simple yearly price.")
        self.assertNotContains(page, "Pay once. Keep your card.")

    def test_cedis_are_the_default_currency_in_ghana_unless_the_visitor_picked_one(self):
        self.assertEqual(self.client.get("/", REMOTE_ADDR=GHANA_IP).wsgi_request.currency, "GHS")
        self.assertEqual(self.client.get("/", REMOTE_ADDR=ABROAD_IP).wsgi_request.currency, "USD")
        self.client.cookies["adsmart_currency"] = "USD"
        self.assertEqual(self.client.get("/", REMOTE_ADDR=GHANA_IP).wsgi_request.currency, "USD")


class OneTimeCheckoutTests(BaseTest):
    def setUp(self):
        self.user = self.make_user()
        self.card = self.make_card(self.user)
        self.client.force_login(self.user)

    def buy(self, plan="professional", ip=GHANA_IP, **extra):
        response = self.client.post(
            "/billing/", {"plan": plan, "action": "pay", "accept_policy": "1", **extra}, REMOTE_ADDR=ip,
        )
        self.assertEqual(response.status_code, 302)
        payment = Payment.objects.latest("pk")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.get(f"/billing/callback?reference={payment.reference}")
        payment.refresh_from_db()
        return payment

    def test_a_ghana_buyer_pays_once_for_a_plan_that_never_ends(self):
        payment = self.buy()
        subscription = Subscription.objects.get(user=self.user)
        self.assertEqual(payment.status, Payment.SUCCESS)
        self.assertEqual((payment.paid_once, payment.months), (True, 0))
        self.assertEqual(payment.subtotal_minor, self.pro.price_minor)  # the plan's own price, charged once
        self.assertIn("paid once", payment.description)
        self.assertIn("paid once", payment.line_items[0]["label"])
        self.assertTrue(subscription.paid_once)
        self.assertEqual(subscription.plan, self.pro)
        self.assertEqual(subscription.expires_at, PAID_ONCE_END)
        self.assertEqual(subscription.state, Subscription.STATE_ACTIVE)
        self.assertFalse(subscription.renews_automatically)
        self.assertTrue(Card.objects.get(pk=self.card.pk).is_live)
        self.assertIn("paid once and never ends", mail.outbox[-1].body)

    def test_nothing_is_saved_for_renewal_even_when_paying_by_card(self):
        payment = self.buy()
        self.assertFalse(payment.set_up_auto_renew)

    def test_outside_ghana_the_plan_is_still_yearly(self):
        payment = self.buy(ip=ABROAD_IP)
        subscription = Subscription.objects.get(user=self.user)
        self.assertEqual((payment.paid_once, payment.months), (False, 12))
        self.assertFalse(subscription.paid_once)
        self.assertTrue(360 < (subscription.expires_at - timezone.now()).days < 370)

    def test_a_live_yearly_plan_stays_yearly_until_it_lapses(self):
        self.subscribe(self.user, self.basic, days=200)
        payment = self.buy()
        self.assertEqual((payment.paid_once, payment.months), (False, 12))
        self.assertFalse(Subscription.objects.get(user=self.user).paid_once)

    def test_a_lapsed_yearly_plan_bought_again_from_ghana_is_paid_once(self):
        self.subscribe(self.user, self.basic, days=-100)  # well past the grace period
        payment = self.buy()
        subscription = Subscription.objects.get(user=self.user)
        self.assertTrue(payment.paid_once)
        self.assertTrue(subscription.paid_once)
        self.assertEqual(subscription.plan, self.pro)
        self.assertLess((timezone.now() - subscription.started_at).days, 1)

    def test_a_stray_second_payment_never_moves_a_plan_paid_once_to_a_cheaper_plan(self):
        self.buy()
        billing.activate(self.user, self.basic, 12, paid_once=True)
        self.assertEqual(Subscription.objects.get(user=self.user).plan, self.pro)

    def test_moving_up_costs_only_the_difference_and_stays_paid_once(self):
        self.buy("basic")
        payment = self.buy("professional")
        subscription = Subscription.objects.get(user=self.user)
        self.assertEqual(payment.purpose, Payment.PURPOSE_UPGRADE)
        self.assertEqual(payment.subtotal_minor, self.pro.price_minor - self.basic.price_minor)
        self.assertEqual((subscription.plan, subscription.paid_once), (self.pro, True))

    def test_there_is_nothing_to_buy_on_the_same_or_a_cheaper_plan(self):
        self.buy("professional")
        before = Payment.objects.count()
        page = self.client.post(
            "/billing/", {"plan": "basic", "action": "pay", "accept_policy": "1"}, REMOTE_ADDR=GHANA_IP, follow=True,
        )
        self.assertEqual(Payment.objects.count(), before)
        self.assertContains(page, "There is nothing to pay.")

    def test_the_checkout_page_shows_cedis_and_no_renewal(self):
        page = self.client.get("/billing/", REMOTE_ADDR=GHANA_IP)
        self.assertContains(page, "paid once")
        self.assertNotContains(page, "Nothing renews")
        self.assertNotContains(page, "Automatic renewal")

    def test_an_nfc_order_with_a_plan_is_paid_once_from_ghana(self):
        quote = _order_quote(1, None, self.pro, None, paid_once=True)
        self.assertEqual(quote["lines"][-1]["label"], f"{self.pro.name} plan, paid once")
        self.assertEqual(_order_quote(1, None, self.pro, None)["lines"][-1]["label"], f"{self.pro.name} plan, 12 months")


class ChangesAfterTheFreeYearTests(BaseTest):
    def paid_once(self, plan=None, months_ago=0, email="ama@example.com"):
        """An account whose plan was paid once ``months_ago`` months ago, with its card made then too."""
        user = self.make_user(email=email)
        card = self.make_card(user, "Ama Boateng")
        when = timezone.now() - relativedelta(months=months_ago)
        subscription = Subscription(user=user, plan=plan or self.pro, started_at=when, expires_at=when)
        subscription.make_paid_once()
        subscription.save()
        billing.activate_cards(user, subscription)
        Card.objects.filter(pk=card.pk).update(created_at=when)
        return user, Card.objects.get(pk=card.pk)

    def edit(self, card, **over):
        return self.client.post(f"/dashboard/cards/{card.pk}/edit/", editor_defaults() | over)

    def test_changes_are_free_for_the_first_year(self):
        user, card = self.paid_once(months_ago=11)
        self.client.force_login(user)
        self.assertFalse(card.edit_locked)
        self.assertEqual(self.edit(card, full_name="Ama B. Boateng").status_code, 302)
        self.assertEqual(Card.objects.get(pk=card.pk).full_name, "Ama B. Boateng")

    def test_a_card_made_before_the_plan_was_bought_gets_its_year_from_the_purchase(self):
        user, card = self.paid_once(months_ago=0)
        Card.objects.filter(pk=card.pk).update(created_at=timezone.now() - relativedelta(months=20))
        self.assertFalse(Card.objects.get(pk=card.pk).edit_locked)

    def test_after_the_first_year_a_save_waits_for_payment(self):
        user, card = self.paid_once(months_ago=13)
        self.client.force_login(user)
        self.assertTrue(card.edit_locked)
        response = self.edit(card, full_name="Ama B. Boateng")
        self.assertEqual(response["Location"], f"/dashboard/cards/{card.pk}/unlock/")
        self.assertEqual(Card.objects.get(pk=card.pk).full_name, "Ama Boateng")  # not on the card yet
        self.assertTrue(CardUpdate.objects.filter(card=card).exists())

    def test_paying_opens_twelve_months_and_puts_the_waiting_change_on_the_card(self):
        user, card = self.paid_once(months_ago=13)
        self.client.force_login(user)
        self.edit(card, full_name="Ama B. Boateng")
        response = self.client.post(f"/dashboard/cards/{card.pk}/unlock/", {"accept_policy": "1", "action": "pay"})
        with self.captureOnCommitCallbacks(execute=True):
            self.client.get(response["Location"])
        card = Card.objects.get(pk=card.pk)
        payment = Payment.objects.get(purpose=Payment.PURPOSE_CARD_UPDATE)
        self.assertEqual(payment.subtotal_minor, 10000)  # GHS 100 on Professional
        self.assertFalse(card.edit_locked)
        self.assertGreater((card.updates_paid_until - timezone.now()).days, 360)
        self.assertEqual(card.full_name, "Ama B. Boateng")
        self.assertFalse(CardUpdate.objects.filter(card=card).exists())

    def test_a_business_team_pays_fifty_for_each_card_it_changes_and_no_other(self):
        user, first = self.paid_once(self.business, months_ago=13)
        second = self.make_card(user, "Kojo Asante")
        Card.objects.filter(pk=second.pk).update(created_at=timezone.now() - relativedelta(months=13))
        self.client.force_login(user)
        self.assertEqual(billing.card_update_quote(first)["total_minor"], 5000)
        response = self.client.post(f"/dashboard/cards/{first.pk}/unlock/", {"accept_policy": "1", "action": "pay"})
        with self.captureOnCommitCallbacks(execute=True):
            self.client.get(response["Location"])
        self.assertFalse(Card.objects.get(pk=first.pk).edit_locked)
        self.assertTrue(Card.objects.get(pk=second.pk).edit_locked)  # the rest of the team is not charged

    def test_a_card_added_later_gets_its_own_free_year(self):
        user, first = self.paid_once(self.business, months_ago=13)
        second = self.make_card(user, "Kojo Asante")  # made today
        self.assertTrue(Card.objects.get(pk=first.pk).edit_locked)
        self.assertFalse(Card.objects.get(pk=second.pk).edit_locked)

    def test_the_business_page_is_never_locked(self):
        user, _card = self.paid_once(months_ago=13)
        self.client.force_login(user)
        self.assertEqual(self.client.get("/dashboard/business/").status_code, 200)

    def test_a_plan_paid_yearly_is_never_asked_to_pay_per_change(self):
        user = self.make_user()
        card = self.make_card(user)
        self.subscribe(user, self.pro)
        Card.objects.filter(pk=card.pk).update(created_at=timezone.now() - relativedelta(months=30))
        self.assertFalse(Card.objects.get(pk=card.pk).edit_locked)

    def test_a_plan_paid_once_never_renews_or_ends(self):
        user, _card = self.paid_once(months_ago=40)
        subscription = Subscription.objects.get(user=user)
        subscription.auto_renew, subscription.authorization_code = True, "AUTH_x"
        self.assertFalse(subscription.renews_automatically)
        self.assertEqual(subscription.state, Subscription.STATE_ACTIVE)
        self.assertEqual(billing.run_auto_renewals(), {"renewed": 0, "failed": 0, "pending": 0})
