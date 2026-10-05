"""Display currencies: dollars by default, pounds, euros, yuan, naira, CFA francs or cedis on request."""

import json
import re
from decimal import Decimal
from unittest import mock

from django.core.cache import cache

from apps.billing import fx
from apps.billing.models import ExchangeRate
from apps.core import currency
from apps.core.currency import COOKIE_NAME

from .helpers import BaseTest

RATES = {"GBP": "0.8", "EUR": "0.9", "CNY": "7", "NGN": "1500", "XOF": "600"}


class CurrencyBase(BaseTest):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.rate = ExchangeRate.objects.create(ghs_per_usd=Decimal("10"), other_rates=RATES, provider="test")
        cache.delete("fx:latest")

    def tearDown(self):
        currency.activate(currency.DEFAULT)
        super().tearDown()

    def page(self, path="/pricing", code=None):
        if code:
            self.client.cookies[COOKIE_NAME] = code
        return self.client.get(path)


class ConversionTests(CurrencyBase):
    def test_a_hundred_cedis_in_every_currency(self):
        # GHS 100 at GHS 10 to the dollar is $10.
        expected = {"USD": "$10", "GBP": "£8", "EUR": "€9", "CNY": "CN¥70", "NGN": "₦15,000", "XOF": "CFA 6,000"}
        for code, text in expected.items():
            self.assertEqual(currency.local_text(10000, self.rate, self.rate, code), text, code)

    def test_cedis_are_never_converted(self):
        self.assertEqual(currency.local_text(10000, self.rate, self.rate, "GHS"), "")

    def test_amounts_that_are_not_whole_keep_two_places(self):
        self.assertEqual(currency.local_text(15000, self.rate, self.rate, "GBP"), "£12")
        self.assertEqual(currency.local_text(12960, self.rate, self.rate, "GBP"), "£10.37")
        self.assertEqual(currency.local_text(12960, self.rate, self.rate, "USD"), "$12.96")

    def test_unknown_rate_falls_back_to_dollars_then_cedis(self):
        bare = ExchangeRate.objects.create(ghs_per_usd=Decimal("10"), provider="test")
        self.assertEqual(currency.effective("GBP", bare), "USD")
        self.assertEqual(currency.local_text(10000, bare, bare, "GBP"), "$10")
        self.assertEqual(currency.local_text(10000, None, None, "GBP"), "")

    def test_a_payment_keeps_the_dollar_value_it_was_made_at(self):
        # Paid when the rate was 12 cedis to the dollar: $8.33 then; in pounds that is 8.33 x 0.8 today.
        self.assertEqual(currency.local_text(10000, Decimal("12"), self.rate, "GBP"), "£6.67")

    def test_calculator_settings_for_the_browser(self):
        config = currency.config_for("GBP", self.rate)
        self.assertEqual(config["symbol"], "£")
        self.assertEqual(Decimal(config["rate"]), Decimal("12.5"))  # cedis for one pound: 10 / 0.8
        self.assertIsNone(currency.config_for("GHS", self.rate)["rate"])
        self.assertEqual(currency.config_for("NGN", self.rate)["decimals"], 0)


class RefreshTests(CurrencyBase):
    def test_refresh_keeps_the_other_currencies_rates(self):
        payload = {"rates": {"GHS": 11.5, "GBP": 0.75, "EUR": 0.86, "CNY": 7.2, "NGN": 1520.5, "XOF": 565, "JPY": 150}}
        with mock.patch("apps.billing.fx.requests.get") as get:
            get.return_value.json.return_value = payload
            get.return_value.raise_for_status.return_value = None
            rate = fx.refresh_rate()
        self.assertEqual(rate.ghs_per_usd, Decimal("11.5"))
        self.assertEqual(set(rate.other_rates), {"GBP", "EUR", "CNY", "NGN", "XOF"})
        self.assertEqual(Decimal(rate.other_rates["NGN"]), Decimal("1520.5"))

    def test_a_missing_currency_is_skipped_not_fatal(self):
        payload = {"rates": {"GHS": 11.5, "GBP": 0.75}}
        with mock.patch("apps.billing.fx.requests.get") as get:
            get.return_value.json.return_value = payload
            get.return_value.raise_for_status.return_value = None
            rate = fx.refresh_rate()
        self.assertEqual(set(rate.other_rates), {"GBP"})


class ChoiceTests(CurrencyBase):
    def test_dollars_by_default(self):
        response = self.page()
        self.assertContains(response, 'plan__usd">$')
        self.assertNotContains(response, 'plan__usd">£')
        self.assertContains(response, '<option value="USD" title="US dollar" selected>')

    def test_the_picker_offers_all_seven(self):
        response = self.page()
        for code in ("USD", "GBP", "EUR", "CNY", "NGN", "XOF", "GHS"):
            self.assertContains(response, f'<option value="{code}"')

    def test_choosing_pounds_changes_the_prices_and_is_remembered(self):
        response = self.client.post("/prefs/", {"currency": "GBP", "next": "/pricing"})
        self.assertRedirects(response, "/pricing", fetch_redirect_response=False)
        self.assertEqual(response.cookies[COOKIE_NAME].value, "GBP")
        page = self.client.get("/pricing")
        self.assertContains(page, 'plan__usd">£')
        self.assertContains(page, '<option value="GBP" title="British pound" selected>')
        # the cedi amount is still there, because that is what is charged
        self.assertRegex(page.content.decode(), r"GHS\s*[\d,]+(\.\d\d)?")

    def test_cedis_alone_when_cedis_are_chosen(self):
        self.client.post("/prefs/", {"currency": "GHS", "next": "/pricing"})
        page = self.client.get("/pricing")
        self.assertNotContains(page, "Approximate. You will be charged")
        self.assertContains(page, "GHS 200")

    def test_bad_values_are_ignored(self):
        response = self.client.post("/prefs/", {"currency": "BTC", "next": "https://evil.example/"})
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertNotIn(COOKIE_NAME, response.cookies)

    def test_prefs_only_accepts_post(self):
        self.assertEqual(self.client.get("/prefs/").status_code, 405)

    def test_a_signed_in_choice_follows_the_account(self):
        user = self.make_user()
        self.client.force_login(user)
        self.client.post("/prefs/", {"currency": "EUR", "next": "/dashboard/"})
        user.refresh_from_db()
        self.assertEqual(user.currency, "EUR")
        self.client.cookies.clear()
        self.client.force_login(user)
        self.assertContains(self.client.get("/pricing"), 'plan__usd">€')

    def test_a_currency_without_a_rate_shows_dollars(self):
        ExchangeRate.objects.create(ghs_per_usd=Decimal("10"), provider="test")
        cache.delete("fx:latest")
        page = self.page(code="GBP")
        self.assertNotContains(page, 'plan__usd">£')
        self.assertContains(page, 'plan__usd">$')

    def test_price_calculators_get_the_chosen_currency(self):
        page = self.page("/nfc", code="NGN")
        raw = re.search(r'id="currency-config" type="application/json">(.*?)</script>', page.content.decode()).group(1)
        config = json.loads(raw)
        self.assertEqual(config["code"], "NGN")
        self.assertEqual(config["symbol"], "₦")
        self.assertEqual(Decimal(config["rate"]), Decimal("0.006667"))  # cedis for one naira: 10 / 1500

    def test_staff_pages_stay_in_dollars(self):
        class Req:
            path = "/staff/payments/"
            COOKIES = {COOKIE_NAME: "GBP"}
            user = None

        self.assertEqual(currency.choice_for(Req()), "USD")

    def test_checkout_pay_label_uses_the_chosen_currency(self):
        label = currency.format_money_local(10000, self.rate)
        self.assertEqual(label, "$10 (GHS 100.00)")
        currency.activate("EUR")
        self.assertEqual(currency.format_money_local(10000, self.rate), "€9 (GHS 100.00)")
        currency.activate("GHS")
        self.assertEqual(currency.format_money_local(10000, self.rate), "GHS 100.00")
