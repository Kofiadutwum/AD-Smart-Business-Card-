"""Languages: the site follows the visitor's phone or browser, they can change it, and nothing breaks."""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.template import Context, Template
from django.utils import translation
from django.utils.translation import check_for_language, gettext

from apps.billing.models import Payment
from apps.core.emails import send_email, subject_text

from .helpers import BaseTest

LANGUAGES = ["fr", "zh-hans", "es", "nl", "pt"]


class LanguageTests(BaseTest):
    # ------------------------------------------------------------------ the catalogs are in place
    def test_every_language_is_installed_and_translates(self):
        for code in LANGUAGES:
            self.assertTrue(check_for_language(code), code)
            with translation.override(code):
                self.assertNotEqual(gettext("Create my card"), "Create my card", code)

    def test_no_sentence_is_left_untranslated(self):
        import polib
        from django.conf import settings

        for code in LANGUAGES:
            path = settings.BASE_DIR / "locale" / translation.to_locale(code) / "LC_MESSAGES" / "django.po"
            po = polib.pofile(str(path))
            self.assertEqual([e.msgid for e in po.untranslated_entries()], [], code)

    # ------------------------------------------------------------------ following the phone or browser
    def home(self, header=None, **cookies):
        for key, value in cookies.items():
            self.client.cookies[key] = value
        extra = {"HTTP_ACCEPT_LANGUAGE": header} if header else {}
        return self.client.get("/", **extra)

    def test_english_by_default_and_for_languages_we_do_not_have(self):
        for header in (None, "de-DE,de;q=0.9", "ja"):
            response = self.home(header)
            self.assertContains(response, "Create my card")
            self.assertContains(response, '<html lang="en"')

    def test_browser_language_picks_the_translation(self):
        expected = {
            "fr-FR,fr;q=0.9,en;q=0.5": ("fr", "Créer ma carte"),
            "zh-CN,zh;q=0.9": ("zh-hans", "创建我的名片"),
            "es-MX,es;q=0.8": ("es", "Crear mi tarjeta"),
            "nl-BE": ("nl", "Mijn kaart maken"),
            "pt-PT,pt;q=0.9": ("pt", "Criar o meu cartão"),
        }
        for header, (code, text) in expected.items():
            response = self.home(header)
            self.assertContains(response, text, msg_prefix=header)
            self.assertContains(response, f'<html lang="{code}"', msg_prefix=header)

    def test_the_choice_made_on_the_site_beats_the_browser(self):
        response = self.home("es", django_language="fr")
        self.assertContains(response, "Créer ma carte")

    def test_changing_language_is_remembered_in_a_cookie_and_on_the_account(self):
        user = self.make_user()
        self.client.force_login(user)
        response = self.client.post("/prefs/", {"language": "nl", "currency": "EUR", "next": "/pricing"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.cookies["django_language"].value, "nl")
        user.refresh_from_db()
        self.assertEqual(user.language, "nl")

    def test_unknown_language_is_ignored(self):
        response = self.client.post("/prefs/", {"language": "xx", "next": "/"})
        self.assertNotIn("django_language", response.cookies)

    def test_a_signed_in_customers_saved_language_is_used_on_a_new_browser(self):
        user = self.make_user(language="pt")
        self.make_card(user)
        self.client.force_login(user)
        response = self.client.get("/dashboard/")
        self.assertContains(response, "Olá, Kwame")

    def test_staff_pages_stay_in_english(self):
        response = self.client.get("/staff/login", HTTP_ACCEPT_LANGUAGE="fr")
        self.assertEqual(response.wsgi_request.LANGUAGE_CODE, "en")

    # ------------------------------------------------------------------ a shared card follows the visitor
    def test_card_page_is_in_the_visitors_language_and_has_a_switch(self):
        user = self.make_user()
        card = self.make_card(user)
        self.subscribe(user, self.pro)
        response = self.client.get(f"/c/{card.slug}", HTTP_ACCEPT_LANGUAGE="zh-CN")
        self.assertContains(response, "保存联系人")
        self.assertContains(response, 'name="language"')

    def test_map_link_keeps_plain_numbers_in_languages_that_use_a_decimal_comma(self):
        user = self.make_user()
        card = self.make_card(user)
        card.latitude, card.longitude = Decimal("5.6037"), Decimal("-0.1870")
        card.save()
        self.subscribe(user, self.pro)
        response = self.client.get(f"/c/{card.slug}", HTTP_ACCEPT_LANGUAGE="fr")
        self.assertRegex(response.content.decode(), r"query=5\.6037\d*,-0\.187\d*")
        self.assertNotRegex(response.content.decode(), r"query=5,6037")

    # ------------------------------------------------------------------ scripts, dates, emails, receipts
    def test_scripts_get_their_sentences_in_the_visitors_language(self):
        import json
        import re

        html = self.home("fr").content.decode()
        table = json.loads(re.search(r'<script id="i18n-strings" type="application/json">(.*?)</script>', html, re.S).group(1))
        self.assertEqual(table["copied"], "Copié")
        self.assertIn("%(label)s", table["theme_change"])

    def test_dates_are_written_the_way_each_language_writes_them(self):
        template = Template('{{ d|ldate:"j F Y" }}')
        expected = {"zh-hans": "2026年10月5日", "es": "5 de octubre de 2026", "fr": "5 octobre 2026", "nl": "5 oktober 2026"}
        for code, text in expected.items():
            with translation.override(code):
                self.assertEqual(template.render(Context({"d": date(2026, 10, 5)})), text, code)
        with translation.override("en"):
            self.assertEqual(template.render(Context({"d": date(2026, 10, 5)})), "5 October 2026")

    def test_customer_emails_use_the_customers_saved_language(self):
        user = self.make_user(language="fr")
        send_email(
            to=user.email, subject=subject_text("Welcome to AD Smart Business Cards"), template="welcome",
            context={"user": user}, user=user, kind="welcome",
        )
        message = mail.outbox[-1]
        self.assertEqual(message.subject, "Bienvenue sur AD Smart Business Cards")
        self.assertIn("Voici comment cela fonctionne", message.body)
        self.assertNotIn("Here is how it works", message.body)

    def test_an_email_to_someone_without_a_saved_language_follows_the_page_they_were_on(self):
        user = self.make_user()
        with translation.override("es"):
            send_email(
                to=user.email, subject=subject_text("Your password was changed"), template="password_changed",
                context={"user": user}, user=user, kind="password_changed",
            )
        self.assertEqual(mail.outbox[-1].subject, "Se ha cambiado tu contraseña")

    def test_emails_to_the_team_stay_in_english(self):
        with translation.override("fr"):
            send_email(
                to="team@example.com", subject="Support request SUP-1", template="staff_message",
                context={"user": mock.Mock(first_name=""), "body": "Hi"}, kind="admin_test",
            )
        self.assertEqual(mail.outbox[-1].subject, "Support request SUP-1")
        self.assertIn("Hello there,", mail.outbox[-1].body)

    def test_pdf_receipts_are_always_in_english(self):
        from apps.billing.receipts import render_receipt_pdf

        seen = []
        with mock.patch(
            "apps.billing.receipts._draw_receipt", side_effect=lambda p: seen.append(translation.get_language()) or b"pdf"
        ):
            with translation.override("zh-hans"):
                render_receipt_pdf(mock.Mock())
        self.assertEqual(seen, ["en"])

    def test_what_is_kept_with_a_payment_is_english_even_when_bought_in_another_language(self):
        user = self.make_user()
        self.make_card(user)
        self.client.force_login(user)
        page = self.client.get("/billing/", HTTP_ACCEPT_LANGUAGE="fr")
        self.assertContains(page, "Formule Professional, 12 mois")
        self.client.post(
            "/billing/", {"action": "pay", "plan": "professional", "accept_policy": "1"}, HTTP_ACCEPT_LANGUAGE="fr"
        )
        payment = Payment.objects.latest("created_at")
        self.assertEqual(payment.line_items[0]["label"], "Professional plan, 12 months")

    # ------------------------------------------------------------------ wording that lives in the database
    def test_delivery_options_and_plan_taglines_are_translated(self):
        response = self.client.get("/nfc-cards", HTTP_ACCEPT_LANGUAGE="fr")
        self.assertContains(response, "Livraison à Cape Coast")
        response = self.client.get("/pricing", HTTP_ACCEPT_LANGUAGE="es")
        self.assertContains(response, "Todo lo que necesitas para compartir una tarjeta.")
