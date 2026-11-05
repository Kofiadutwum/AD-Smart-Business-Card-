"""Support resets the password of a customer who lost both password and email."""

import re
from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from apps.accounts.models import OneTimeCode, User
from apps.accounts.services import issue_reset_code
from apps.core.models import AuditLog

from .helpers import BaseTest

CHECKS = {"checked_name": "on", "checked_phone": "on", "checked_activity": "on", "reason": "Caller lost phone and email"}
NEW = {"password": "My-own-pass-2026", "password_confirm": "My-own-pass-2026"}
OTP_PAGE = "/auth/one-time-password"


class StaffPasswordResetTests(BaseTest):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.customer = self.make_user(phone="+233244000111")
        self.make_card(self.customer)
        self.url = f"/staff/subscribers/{self.customer.pk}/reset-password/"

    def login_staff(self, role="support"):
        staff = User.objects.create_user(
            email=f"{role}@example.com", password="Staff-pass-2026", is_staff=True, staff_role=role
        )
        self.client.post("/staff/login", {"email": staff.email, "password": "Staff-pass-2026"})
        return staff

    def reset(self, data=None):
        self.login_staff()
        mail.outbox.clear()
        return self.client.post(self.url, data or CHECKS)

    def use(self, code, phone="0244000111", client=None, **extra):
        return (client or Client()).post(OTP_PAGE, {"phone": phone, "code": code, **NEW, **extra})

    def test_support_gets_a_one_time_password_with_the_page_and_phone_to_give(self):
        response = self.reset()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, response.context["temporary"])
        self.assertContains(response, OTP_PAGE)
        self.assertContains(response, "+233 24 400 0111")
        self.assertContains(response, self.customer.email)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_the_customer_sets_a_new_password_with_their_phone_and_the_one_time_password(self):
        code = self.reset().context["temporary"]
        customer_client = Client()
        response = self.use(code, client=customer_client)
        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)
        page = customer_client.get("/dashboard/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f"sign in with {self.customer.email}")
        self.customer.refresh_from_db()
        self.assertIsNone(self.customer.password_temp_until)
        self.assertTrue(self.customer.check_password("My-own-pass-2026"))
        self.assertFalse(self.customer.check_password(code))

    def test_the_password_can_be_typed_in_lower_case_and_without_the_dash(self):
        code = self.reset().context["temporary"]
        response = self.use(code.replace("-", "").lower(), phone="+233 24 400 0111")
        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)

    def test_it_works_once_then_it_is_dead(self):
        code = self.reset().context["temporary"]
        self.use(code)
        again = self.use(code, password="Another-pass-2026", password_confirm="Another-pass-2026")
        self.assertContains(again, "do not match", status_code=400)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.check_password("My-own-pass-2026"))

    def test_a_wrong_phone_or_code_changes_nothing_and_does_not_burn_the_password(self):
        code = self.reset().context["temporary"]
        self.assertEqual(self.use(code, phone="0244999999").status_code, 400)
        self.assertEqual(self.use("AAAAA-BBBBB").status_code, 400)
        self.assertIsNotNone(User.objects.get(pk=self.customer.pk).password_temp_until)
        self.assertRedirects(self.use(code), "/dashboard/", fetch_redirect_response=False)

    def test_mismatched_new_passwords_do_not_burn_it_either(self):
        code = self.reset().context["temporary"]
        bad = self.use(code, password_confirm="something-else")
        self.assertEqual(bad.status_code, 200)
        self.assertRedirects(self.use(code), "/dashboard/", fetch_redirect_response=False)

    def test_the_normal_login_does_not_take_the_one_time_password(self):
        code = self.reset().context["temporary"]
        response = Client().post("/auth/login", {"email": self.customer.email, "password": code})
        self.assertContains(response, "one-time password from support", status_code=403)

    def test_the_login_and_forgot_pages_link_to_it(self):
        for page in ("/auth/login", "/auth/forgot-password"):
            self.assertContains(self.client.get(page), f'href="{OTP_PAGE}"')

    def test_every_device_the_customer_was_signed_in_on_is_signed_out(self):
        phone = Client()
        phone.force_login(self.customer)
        self.assertEqual(phone.get("/dashboard/").status_code, 200)
        self.reset()
        response = phone.get("/dashboard/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login", response["Location"])

    def test_the_account_email_gets_a_notice_with_the_page_but_not_the_password(self):
        response = self.reset()
        notice = [m for m in mail.outbox if m.to == [self.customer.email]]
        self.assertEqual(len(notice), 1)
        self.assertIn("reset the password", notice[0].body)
        self.assertIn(OTP_PAGE, notice[0].body)
        self.assertNotIn(response.context["temporary"], notice[0].body)

    def test_the_reset_is_in_the_audit_log_with_who_and_why(self):
        self.reset()
        entry = AuditLog.objects.get(action="customer_password_reset")
        self.assertEqual(entry.actor.email, "support@example.com")
        self.assertEqual(entry.reason, "Caller lost phone and email")
        self.assertEqual(entry.target_id, str(self.customer.pk))

    def test_a_missing_check_or_reason_changes_nothing(self):
        self.login_staff()
        before = self.customer.password
        for bad in ({**CHECKS, "checked_phone": ""}, {**CHECKS, "reason": ""}, {}):
            self.client.post(self.url, bad)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.password, before)
        self.assertIsNone(self.customer.password_temp_until)
        self.assertFalse(AuditLog.objects.filter(action="customer_password_reset").exists())

    def test_the_one_time_password_stops_working_after_48_hours(self):
        code = self.reset().context["temporary"]
        User.objects.filter(pk=self.customer.pk).update(password_temp_until=timezone.now() - timedelta(minutes=1))
        self.assertContains(self.use(code), "or the password has expired", status_code=400)

    def test_five_wrong_tries_on_one_phone_lock_it_for_15_minutes(self):
        code = self.reset().context["temporary"]
        for _ in range(5):
            self.use("AAAAA-BBBBB")
        self.assertEqual(self.use(code).status_code, 429)

    def test_a_suspended_account_cannot_use_it(self):
        code = self.reset().context["temporary"]
        User.objects.filter(pk=self.customer.pk).update(is_suspended=True)
        self.assertEqual(self.use(code).status_code, 403)

    def test_a_code_sent_by_email_before_the_reset_no_longer_works(self):
        issue_reset_code(self.customer)
        self.reset()
        self.assertFalse(
            OneTimeCode.objects.filter(
                user=self.customer, purpose=OneTimeCode.PURPOSE_PASSWORD_RESET, used_at__isnull=True
            ).exists()
        )

    def test_choosing_a_password_by_emailed_code_also_clears_the_one_time_password(self):
        self.reset()
        mail.outbox.clear()
        client = Client()
        client.post("/auth/forgot-password", {"email": self.customer.email})
        code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
        client.post("/auth/forgot-password/verify", {
            "code": code, "password": "Fresh-pass-2026", "password_confirm": "Fresh-pass-2026",
        })
        self.customer.refresh_from_db()
        self.assertIsNone(self.customer.password_temp_until)
        self.assertTrue(self.customer.check_password("Fresh-pass-2026"))

    def test_only_super_and_support_may_do_it(self):
        for role, allowed in (("finance", False), ("design", False), ("ops", False), ("super", True), ("support", True)):
            client = Client()
            staff = User.objects.create_user(
                email=f"{role}-x@example.com", password="Staff-pass-2026", is_staff=True, staff_role=role
            )
            client.post("/staff/login", {"email": staff.email, "password": "Staff-pass-2026"})
            self.assertEqual(client.post(self.url, CHECKS).status_code, 200 if allowed else 403, role)
            page = client.get(f"/staff/subscribers/{self.customer.pk}/")
            self.assertEqual(b"Lost password and email" in page.content, allowed, role)

    def test_a_staff_account_cannot_be_reset_this_way(self):
        self.login_staff()
        other = User.objects.create_user(email="boss@example.com", password="x", is_staff=True, staff_role="super")
        self.assertEqual(self.client.post(f"/staff/subscribers/{other.pk}/reset-password/", CHECKS).status_code, 404)
        other.refresh_from_db()
        self.assertTrue(other.check_password("x"))

    def test_a_deleted_account_is_not_reset(self):
        User.objects.filter(pk=self.customer.pk).update(anonymised_at=timezone.now())
        self.login_staff()
        self.assertEqual(self.client.post(self.url, CHECKS).status_code, 302)
        self.assertFalse(AuditLog.objects.filter(action="customer_password_reset").exists())

    def test_an_account_with_no_phone_is_told_why_nothing_happened(self):
        User.objects.filter(pk=self.customer.pk).update(phone=None)
        self.login_staff()
        response = self.client.post(self.url, CHECKS, follow=True)
        self.assertContains(response, "This account has no phone number")
        self.assertFalse(AuditLog.objects.filter(action="customer_password_reset").exists())


class ForgotPasswordPageTests(BaseTest):
    def test_the_page_points_people_who_lost_their_email_to_support(self):
        page = self.client.get("/auth/forgot-password")
        self.assertContains(page, "Lost access to that email too?")
        self.assertContains(page, 'href="/contact"')
