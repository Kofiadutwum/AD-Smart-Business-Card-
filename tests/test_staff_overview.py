"""The staff Overview graphs, and Ghana and outside Ghana kept as separate accounts (the prices differ)."""

from datetime import timedelta
from unittest import mock

from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import Payment
from apps.cards.models import CardEvent
from apps.staff.regions import ONE_TIME_PRICES_FROM

from .helpers import BaseTest


class GraphsAndRegionsTests(BaseTest):
    def setUp(self):
        self.boss = User.objects.create_user(email="boss@example.com", password="Staff-pass-2026", is_staff=True, staff_role="super")
        self.ama = self.make_user("ama@example.com", full_name="Ama Owusu")  # Ghana: her plan was paid once
        self.ama_card = self.make_card(self.ama, "Ama Owusu")
        plan = self.subscribe(self.ama, self.pro)
        plan.make_paid_once()
        plan.save()
        self.john = self.make_user("john@example.com", full_name="John Smith")  # outside Ghana: a yearly plan
        self.john_card = self.make_card(self.john, "John Smith")
        self.subscribe(self.john, self.pro)
        self.pay(self.ama, 50000, paid_once=True)
        self.pay(self.john, 15000)

    def pay(self, user, amount, **fields):
        number = Payment.objects.count()
        return Payment.objects.create(
            user=user, reference=f"ADS-T-{number}", receipt_number=f"ADR-T-{number}", purpose=Payment.PURPOSE_SUBSCRIPTION,
            plan=self.pro, subtotal_minor=amount, amount_minor=amount, status=Payment.SUCCESS,
            paid_at=timezone.now() - timedelta(minutes=1), **fields,
        )

    def overview(self, user=None):
        self.client.force_login(user or self.boss)
        response = self.client.get(reverse("staff:home"))
        self.assertEqual(response.status_code, 200)
        return response, {chart["id"]: chart for chart in response.context["charts"]}

    def test_revenue_graph_and_figures_keep_ghana_and_outside_apart(self):
        response, charts = self.overview()
        revenue = charts["revenue"]["data"]
        totals = [sum(revenue[region]["12m"]["values"]["revenue"]) for region in ("all", "ghana", "abroad")]
        self.assertEqual(totals, [65000, 50000, 15000])
        self.assertEqual(sum(revenue["all"]["7d"]["values"]["revenue"]), 65000)
        self.assertContains(response, "Ghana GHS 500.00<br>Outside Ghana GHS 150.00")
        self.assertContains(response, 'id="staff-charts"')
        self.assertContains(response, "js/staff-charts.js")
        # Without scripting the first span still reads as a table.
        self.assertContains(response, timezone.localdate().strftime("%B %Y"))

    def test_sign_ups_and_card_activity_split_too(self):
        CardEvent.objects.create(card=self.ama_card, kind=CardEvent.VIEW)
        CardEvent.objects.create(card=self.john_card, kind=CardEvent.VIEW)
        CardEvent.objects.create(card=self.john_card, kind=CardEvent.SAVE_CONTACT)
        CardEvent.objects.create(card=self.john_card, kind=CardEvent.CALL)
        _response, charts = self.overview()
        activity = charts["activity"]["data"]
        self.assertEqual(sum(activity["all"]["7d"]["values"]["views"]), 2)
        self.assertEqual(sum(activity["ghana"]["7d"]["values"]["views"]), 1)
        self.assertEqual([sum(activity["abroad"]["7d"]["values"][k]) for k in ("views", "saves", "taps")], [1, 1, 1])
        signups = charts["signups"]["data"]
        self.assertEqual([sum(signups[r]["30d"]["values"]["signups"]) for r in ("all", "ghana", "abroad")], [2, 1, 1])
        self.assertEqual(len(signups["all"]["7d"]["labels"]), 7)
        self.assertEqual(len(signups["all"]["12m"]["labels"]), 12)

    def test_roles_that_do_not_see_payments_get_no_money_graph(self):
        designer = User.objects.create_user(email="design@example.com", password="Staff-pass-2026", is_staff=True, staff_role="design")
        response, charts = self.overview(designer)
        self.assertEqual(list(charts), ["signups", "work", "activity"])
        self.assertNotContains(response, "Revenue this month")
        self.assertNotContains(response, "GHS 500.00")
        # Bars open only the lists the role may open: NFC orders yes, support requests no.
        self.assertEqual(charts["work"]["links"][0], reverse("staff:nfc_orders") + "?queue=new")
        self.assertIsNone(charts["work"]["links"][-1])

    def test_graph_points_link_to_the_lists_behind_them(self):
        _response, charts = self.overview()
        self.assertEqual(charts["revenue"]["link"], reverse("staff:payments") + "?status=success&from={from}&to={to}&region={region}")
        self.assertEqual(charts["signups"]["link"], reverse("staff:subscribers") + "?from={from}&to={to}&region={region}")
        self.assertEqual(len(charts["work"]["links"]), len(charts["work"]["data"]["all"]["30d"]["labels"]))
        self.assertEqual(charts["work"]["links"][-1], reverse("staff:support") + "?status=resolved")
        self.assertNotIn("link", charts["activity"])
        # The lists those links open take the region and the dates.
        today = timezone.localdate().isoformat()
        ghana = self.client.get(reverse("staff:payments") + f"?status=success&from={today}&to={today}&region=ghana")
        self.assertEqual(ghana.context["totals"]["count"], 1)
        self.assertContains(ghana, "ama@example.com")
        self.assertNotContains(ghana, "john@example.com")
        joined = self.client.get(reverse("staff:subscribers") + f"?from={today}&to={today}&region=abroad")
        self.assertContains(joined, "john@example.com")
        self.assertNotContains(joined, "ama@example.com")
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertNotContains(self.client.get(reverse("staff:subscribers") + f"?from={tomorrow}"), "john@example.com")
        self.assertContains(self.client.get(reverse("staff:subscribers") + "?from=not-a-date"), "john@example.com")

    def test_a_failed_graph_query_leaves_the_overview_working(self):
        self.client.force_login(self.boss)
        with mock.patch("apps.staff.views.overview._charts", side_effect=RuntimeError("boom")), \
                self.assertLogs("apps.staff.views.overview", "ERROR"):
            response = self.client.get(reverse("staff:home"))
        self.assertContains(response, "The graphs could not load.")
        self.assertContains(response, "Recent registrations")

    def test_everything_before_the_one_time_prices_counts_as_ghana(self):
        kojo = self.make_user("kojo@example.com", full_name="Kojo Mensah")
        self.subscribe(kojo, self.pro)  # a yearly plan, bought before one-time prices existed
        before = ONE_TIME_PRICES_FROM - timedelta(days=30)
        User.objects.filter(pk=kojo.pk).update(date_joined=before)
        old = self.pay(kojo, 20000)
        Payment.objects.filter(pk=old.pk).update(created_at=before, paid_at=before)
        response, charts = self.overview()
        revenue = charts["revenue"]["data"]
        self.assertEqual([sum(revenue[r]["12m"]["values"]["revenue"]) for r in ("all", "ghana", "abroad")], [85000, 70000, 15000])
        for name in ("staff:subscribers", "staff:payments"):
            self.assertContains(self.client.get(reverse(name) + "?region=ghana"), "kojo@example.com")
            self.assertNotContains(self.client.get(reverse(name) + "?region=abroad"), "kojo@example.com")

    def test_the_finance_report_is_kept_per_region(self):
        self.client.force_login(self.boss)
        url = reverse("staff:finance_report")
        for query, expected in (("", 65000), ("?region=ghana", 50000), ("?region=abroad", 15000)):
            response = self.client.get(url + query)
            self.assertEqual(sum(month["subscription"] for _key, month in response.context["rows"]), expected, query)
        self.assertContains(response, 'aria-current="page">Outside Ghana')

    def test_subscribers_can_be_listed_per_region(self):
        self.client.force_login(self.boss)
        url = reverse("staff:subscribers")
        ghana = self.client.get(url + "?region=ghana")
        self.assertContains(ghana, "ama@example.com")
        self.assertNotContains(ghana, "john@example.com")
        abroad = self.client.get(url + "?region=abroad")
        self.assertContains(abroad, "john@example.com")
        self.assertNotContains(abroad, "ama@example.com")
        self.assertContains(self.client.get(url), "Outside Ghana</span>")
