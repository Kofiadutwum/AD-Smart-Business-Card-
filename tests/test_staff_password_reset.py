"""Support resets the password of a customer who lost both password and email."""

import re
from datetime import timedelta

from django.core import mail
from django.test import Client
from django.utils import timezone

from apps.accounts.models import OneTimeCode, User
from apps.accounts.services import issue_reset_code
from apps.core.models import AuditLog

from .helpers import BaseTest

CHECKS = {"checked_name": "on", "checked_phone": "on", "checked_activity": "on", "reason": "Caller lost phone and email"}


class StaffPasswordResetTests(BaseTest):
    def setUp(self):
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

    def test_support_gets_a_temporary_password_and_the_email_to_read_out(self):
        response = self.reset()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.customer.email)
        self.assertContains(response, response.context["temporary"])
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_the_customer_signs_in_with_it_and_must_choose_their_own_before_anything_else(self):
        temporary = self.reset().context["temporary"]
        customer_client = Client()
        signed_in = customer_client.post("/auth/login", {"email": self.customer.email, "password": temporary})
        self.assertEqual(signed_in.status_code, 302)
        self.assertRedirects(customer_client.get("/dashboard/"), "/auth/choose-password", fetch_redirect_response=False)
        done = customer_client.post("/auth/choose-password", {
            "current_password": temporary, "password": "My-own-pass-2026", "password_confirm": "My-own-pass-2026",
        })
        self.assertRedirects(done, "/dashboard/", fetch_redirect_response=False)
        self.assertEqual(customer_client.get("/dashboard/").status_code, 200)
        self.customer.refresh_from_db()
        self.assertIsNone(self.customer.password_temp_until)
        self.assertTrue(self.customer.check_password("My-own-pass-2026"))
        self.assertFalse(self.customer.check_password(temporary))

    def test_the_choose_password_page_is_closed_to_everyone_else(self):
        self.client.force_login(self.customer)
        self.assertRedirects(self.client.get("/auth/choose-password"), "/dashboard/", fetch_redirect_response=False)

    def test_every_device_the_customer_was_signed_in_on_is_signed_out(self):
        phone = Client()
        phone.force_login(self.customer)
        self.assertEqual(phone.get("/dashboard/").status_code, 200)
        self.reset()
        response = phone.get("/dashboard/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login", response["Location"])

    def test_the_account_email_gets_a_notice_without_the_password(self):
        response = self.reset()
        notice = [m for m in mail.outbox if m.to == [self.customer.email]]
        self.assertEqual(len(notice), 1)
        self.assertIn("reset the password", notice[0].body)
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

    def test_the_temporary_password_stops_working_after_48_hours(self):
        temporary = self.reset().context["temporary"]
        User.objects.filter(pk=self.customer.pk).update(password_temp_until=timezone.now() - timedelta(minutes=1))
        response = Client().post("/auth/login", {"email": self.customer.email, "password": temporary})
        self.assertContains(response, "This temporary password has expired", status_code=403)

    def test_a_code_sent_by_email_before_the_reset_no_longer_works(self):
        issue_reset_code(self.customer)
        self.reset()
        self.assertFalse(
            OneTimeCode.objects.filter(
                user=self.customer, purpose=OneTimeCode.PURPOSE_PASSWORD_RESET, used_at__isnull=True
            ).exists()
        )

    def test_choosing_a_password_by_emailed_code_also_clears_the_temporary_flag(self):
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
