"""The live email check, and customers (or support) changing the email on an account."""

from datetime import timedelta
from unittest import mock

import dns.exception
import dns.resolver
from django.core import mail
from django.core.cache import cache
from django.test import Client, override_settings
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import User
from apps.core.models import AuditLog

from .helpers import BaseTest

PASSWORD = "Str0ng-pass-123"
CHECK = "/auth/check-email"
CHANGE = "/auth/email"
STAFF_CHECKS = {"checked_name": "on", "checked_phone": "on", "checked_activity": "on", "reason": "Lost the old inbox"}


class CheckEmailTests(BaseTest):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def ask(self, email, client=None):
        return (client or self.client).get(CHECK, {"email": email}).json()

    def test_a_good_address_gets_a_green_light(self):
        self.assertEqual(self.ask("ama@example.com")["status"], "ok")

    def test_a_badly_formed_address_is_an_error(self):
        data = self.ask("ama@@example")
        self.assertEqual(data["status"], "error")
        self.assertIn("does not look like an email", data["message"])

    def test_an_address_that_already_has_an_account_is_an_error(self):
        self.make_user(email="kwame@example.com")
        data = self.ask("KWAME@example.com")
        self.assertEqual(data["status"], "error")
        self.assertIn("already has an account", data["message"])

    def test_a_mistyped_popular_domain_gets_a_fix_to_click(self):
        for typo in ("ama@gmial.com", "ama@gmail.con", "ama@yahooo.com"):
            data = self.ask(typo)
            self.assertEqual(data["status"], "warn", typo)
            self.assertTrue(data["suggestion"].startswith("ama@"), typo)
        self.assertEqual(self.ask("ama@gmial.com")["suggestion"], "ama@gmail.com")

    def test_a_correct_popular_domain_gets_no_hint(self):
        self.assertEqual(self.ask("ama@gmail.com")["status"], "ok")

    @override_settings(EMAIL_DOMAIN_CHECK=True)
    def test_a_domain_with_no_mail_server_is_an_error(self):
        with mock.patch("apps.accounts.services._domain_can_receive_mail", return_value=False):
            data = self.ask("ama@no-such-place.example")
        self.assertEqual(data["status"], "error")
        self.assertIn("no-such-place.example", data["message"])

    def test_a_signed_in_customer_is_told_when_it_is_their_own_address(self):
        user = self.make_user()
        client = Client()
        client.force_login(user)
        self.assertIn("already your email", self.ask("kwame@example.com", client)["message"])

    def test_too_many_checks_from_one_connection_are_refused(self):
        with mock.patch("apps.accounts.views.rate_limited", return_value=True):
            self.assertEqual(self.client.get(CHECK, {"email": "ama@example.com"}).status_code, 429)

    @override_settings(EMAIL_DOMAIN_CHECK=True)
    def test_sign_up_refuses_an_address_with_no_mail_server(self):
        with mock.patch("apps.accounts.services._domain_can_receive_mail", return_value=False):
            response = self.client.post("/auth/register", {
                "full_name": "Efua Owusu", "email": "efua@no-such-place.example", "phone": "024 412 3456",
                "password": "Card-maker-2026", "accept_terms": "on",
            })
        self.assertContains(response, "could not find a mail server")
        self.assertFalse(User.objects.filter(email="efua@no-such-place.example").exists())

    def test_the_boxes_ask_the_live_check(self):
        self.assertContains(self.client.get("/auth/register"), f'data-email-check="{CHECK}"')


class MailServerLookupTests(BaseTest):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def lookup(self, domain, **kw):
        with mock.patch("dns.resolver.resolve", **kw):
            return services._domain_can_receive_mail(domain)

    def test_a_domain_with_a_mail_server_is_fine(self):
        record = mock.Mock(exchange="mx.example.org.")
        self.assertTrue(self.lookup("example.org", return_value=[record]))

    def test_a_domain_that_does_not_exist_is_not(self):
        self.assertFalse(self.lookup("gone.example", side_effect=dns.resolver.NXDOMAIN))

    def test_a_null_mx_means_no_mail(self):
        self.assertFalse(self.lookup("nomail.example", return_value=[mock.Mock(exchange=".")]))

    def test_no_mx_falls_back_to_the_domains_own_address(self):
        calls = [dns.resolver.NoAnswer(), ["203.0.113.5"]]
        self.assertTrue(self.lookup("plain.example", side_effect=calls))
        self.assertFalse(self.lookup("empty.example", side_effect=[dns.resolver.NoAnswer(), dns.resolver.NoAnswer()]))

    def test_a_slow_or_broken_lookup_never_blocks_a_customer(self):
        self.assertTrue(self.lookup("slow.example", side_effect=dns.exception.Timeout))


class ChangeEmailTests(BaseTest):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = self.make_user(phone="+233244000111")
        self.make_card(self.user)
        self.client.force_login(self.user)

    def change(self, email="new@example.com", password=PASSWORD, client=None):
        return (client or self.client).post(CHANGE, {"email": email, "current_password": password})

    def test_settings_links_to_it(self):
        self.assertContains(self.client.get("/dashboard/settings/"), f'href="{CHANGE}"')

    def test_the_customer_changes_their_email_and_logs_in_with_the_new_one(self):
        response = self.change()
        self.assertRedirects(response, "/dashboard/settings/", fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "new@example.com")
        self.assertIsNotNone(self.user.email_changed_at)
        login = Client().post("/auth/login", {"email": "new@example.com", "password": PASSWORD})
        self.assertEqual(login.status_code, 302)
        self.assertEqual(self.client.get("/dashboard/").status_code, 200)  # this device stays signed in

    def test_the_old_address_is_told_and_the_change_is_logged(self):
        self.change()
        notice = [m for m in mail.outbox if m.to == ["kwame@example.com"]]
        self.assertEqual(len(notice), 1)
        self.assertIn("new@example.com", notice[0].body)
        entry = AuditLog.objects.get(action="email_changed")
        self.assertEqual(entry.details, {"old": "kwame@example.com", "new": "new@example.com"})

    def test_every_other_device_is_signed_out(self):
        other = Client()
        other.force_login(self.user)
        self.assertEqual(other.get("/dashboard/").status_code, 200)
        self.change()
        response = other.get("/dashboard/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login", response["Location"])

    def test_a_wrong_password_changes_nothing(self):
        response = self.change(password="not-it")
        self.assertContains(response, "not your current password")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "kwame@example.com")
        self.assertFalse(AuditLog.objects.filter(action="email_changed").exists())

    def test_an_address_on_another_account_is_refused(self):
        self.make_user(email="taken@example.com", phone="+233244000222")
        self.assertContains(self.change(email="Taken@Example.com"), "belongs to another account")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "kwame@example.com")

    def test_the_same_address_is_refused(self):
        self.assertContains(self.change(email="kwame@example.com"), "already your email")

    def test_only_once_every_90_days(self):
        self.change()
        User.objects.filter(pk=self.user.pk).update(email_changed_at=timezone.now() - timedelta(days=30))
        response = self.change(email="third@example.com", password=PASSWORD)
        self.assertContains(response, "You changed your email recently", status_code=200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "new@example.com")

    def test_after_90_days_it_can_be_changed_again(self):
        User.objects.filter(pk=self.user.pk).update(email_changed_at=timezone.now() - timedelta(days=91))
        self.assertRedirects(self.change(), "/dashboard/settings/", fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertGreater(self.user.email_changed_at, timezone.now() - timedelta(minutes=1))

    def test_a_typo_can_be_fixed_within_24_hours_without_starting_a_new_wait(self):
        self.change(email="typo@example.com")
        self.user.refresh_from_db()
        first = self.user.email_changed_at
        self.assertRedirects(self.change(email="right@example.com"), "/dashboard/settings/", fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "right@example.com")
        self.assertEqual(self.user.email_changed_at, first)  # the clock kept its original start

    def test_the_fix_window_closes_after_24_hours(self):
        self.change(email="typo@example.com")
        User.objects.filter(pk=self.user.pk).update(email_changed_at=timezone.now() - timedelta(hours=25))
        self.assertContains(self.change(email="right@example.com"), "You changed your email recently")

    def test_five_attempts_an_hour_then_a_lock(self):
        for _ in range(5):
            self.change(password="not-it")
        self.assertEqual(User.objects.get(pk=self.user.pk).email, "kwame@example.com")
        self.assertContains(self.change(), "Too many attempts", status_code=200)

    def test_a_google_only_account_is_asked_to_set_a_password_first(self):
        google = User.objects.create_user(email="g@example.com", password=None, google_sub="g-1", phone="+233244000999")
        client = Client()
        client.force_login(google)
        page = client.get(CHANGE)
        self.assertContains(page, "no password yet")
        self.assertNotContains(page, 'name="current_password"')

    def test_it_needs_a_login(self):
        self.assertEqual(Client().get(CHANGE).status_code, 302)


class StaffChangeEmailTests(BaseTest):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.customer = self.make_user(phone="+233244000111")
        self.url = f"/staff/subscribers/{self.customer.pk}/change-email/"

    def staff_client(self, role):
        client = Client()
        User.objects.create_user(email=f"{role}@example.com", password="Staff-pass-2026", is_staff=True, staff_role=role)
        client.post("/staff/login", {"email": f"{role}@example.com", "password": "Staff-pass-2026"})
        return client

    def post(self, client, **over):
        return client.post(self.url, {"new_email": "fresh@example.com", **STAFF_CHECKS, **over})

    def test_support_changes_the_email_and_the_old_address_is_told(self):
        client = self.staff_client("support")
        mail.outbox.clear()
        self.post(client)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.email, "fresh@example.com")
        self.assertEqual([m.to for m in mail.outbox], [["kwame@example.com"]])
        entry = AuditLog.objects.get(action="customer_email_changed")
        self.assertEqual(entry.reason, "Lost the old inbox")
        self.assertEqual(entry.details, {"old": "kwame@example.com", "new": "fresh@example.com"})
        self.assertTrue(self.customer.check_password(PASSWORD))  # their password still works

    def test_a_missing_check_or_a_taken_address_changes_nothing(self):
        self.make_user(email="taken@example.com", phone="+233244000222")
        client = self.staff_client("support")
        self.post(client, checked_phone="")
        self.post(client, new_email="taken@example.com")
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.email, "kwame@example.com")
        self.assertFalse(AuditLog.objects.filter(action="customer_email_changed").exists())

    def test_only_super_and_support_may_do_it(self):
        for role, allowed in (("finance", False), ("design", False), ("ops", False), ("super", True)):
            client = self.staff_client(role)
            self.assertEqual(self.post(client, new_email=f"{role}-new@example.com").status_code, 302 if allowed else 403, role)
            page = client.get(f"/staff/subscribers/{self.customer.pk}/")
            self.assertEqual(b"For a customer who can no longer reach their email" in page.content, allowed, role)
