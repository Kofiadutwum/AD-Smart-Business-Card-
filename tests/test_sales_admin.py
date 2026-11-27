"""The Sales Admin role and the staff inbox: admins message each other, often about a customer's card."""

from dateutil.relativedelta import relativedelta
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.cards.models import Card
from apps.core.models import AuditLog
from apps.staff import inbox
from apps.staff.models import StaffThread

from .helpers import BaseTest
from .test_pay_to_update import editor_defaults


class SalesAdminTests(BaseTest):
    def setUp(self):
        self.sales = User.objects.create_user(email="sales@example.com", password="x", is_staff=True, staff_role="sales")
        self.customer = self.make_user("ama@example.com", full_name="Ama Boateng")
        self.card = self.make_card(self.customer, "Ama Boateng")
        self.subscribe(self.customer, self.pro)
        self.client.force_login(self.sales)

    def test_the_role_is_named_sales_admin(self):
        self.assertEqual(self.sales.role_label, "Sales Admin")

    def test_sales_see_support_customers_and_cards_but_no_money(self):
        for name in ("staff:support", "staff:subscribers", "staff:messages"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("staff:subscriber", args=[self.customer.pk])).status_code, 200)
        for name in ("staff:payments", "staff:finance_report", "staff:nfc_orders", "staff:premade", "staff:settings"):
            self.assertNotEqual(self.client.get(reverse(name)).status_code, 200, name)
        page = self.client.get(reverse("staff:home"))
        self.assertNotContains(page, "Revenue this month")

    def test_sales_edit_a_card_for_its_owner(self):
        url = reverse("staff:card_edit", args=[self.card.pk])
        self.assertContains(self.client.get(url), "Save card")
        response = self.client.post(url, {**editor_defaults(), "job_title": "Wedding photographer"})
        self.assertRedirects(response, reverse("staff:subscriber", args=[self.customer.pk]), fetch_redirect_response=False)
        self.card.refresh_from_db()
        self.assertEqual(self.card.job_title, "Wedding photographer")
        self.assertTrue(AuditLog.objects.filter(action="staff_card_edited", actor=self.sales).exists())

    def test_a_locked_card_waits_for_its_owner_to_pay(self):
        self.card.update_plan = self.basic
        self.card.save(update_fields=["update_plan"])
        Card.objects.filter(pk=self.card.pk).update(created_at=timezone.now() - relativedelta(months=13))
        url = reverse("staff:card_edit", args=[self.card.pk])
        self.assertContains(self.client.get(url), "Locked.")
        self.client.post(url, {**editor_defaults(), "job_title": "Changed"})
        self.card.refresh_from_db()
        self.assertNotEqual(self.card.job_title, "Changed")

    def test_other_roles_cannot_edit_cards(self):
        self.client.force_login(User.objects.create_user(email="design@example.com", password="x", is_staff=True, staff_role="design"))
        self.assertEqual(self.client.get(reverse("staff:card_edit", args=[self.card.pk])).status_code, 403)


class InboxTests(BaseTest):
    def setUp(self):
        self.sales = User.objects.create_user(email="sales@example.com", password="x", is_staff=True, staff_role="sales")
        self.boss = User.objects.create_user(email="boss@example.com", password="x", is_staff=True, staff_role="super")
        self.design = User.objects.create_user(email="design@example.com", password="x", is_staff=True, staff_role="design")
        self.customer = self.make_user("ama@example.com", full_name="Ama Boateng")
        self.card = self.make_card(self.customer, "Ama Boateng")

    def send(self):
        self.client.force_login(self.sales)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("staff:message_new"), {
                "card": self.card.pk, "to": ["super"], "subject": "Wrong logo", "body": "Ama wants her new logo on the card.",
            })
        return response, StaffThread.objects.get()

    def test_a_message_about_a_card_goes_to_the_roles_picked_and_is_emailed(self):
        response, thread = self.send()
        self.assertRedirects(response, reverse("staff:message", args=[thread.pk]), fetch_redirect_response=False)
        self.assertEqual((thread.card, thread.customer, thread.role_list), (self.card, self.customer, ["super"]))
        self.assertEqual([m.to for m in mail.outbox], [["boss@example.com"]])
        self.assertIn("Wrong logo", mail.outbox[0].subject)
        self.assertEqual(list(inbox.visible(self.design)), [])
        self.assertEqual(self.client.get(reverse("staff:message", args=[thread.pk])).status_code, 200)
        self.client.force_login(self.design)
        self.assertEqual(self.client.get(reverse("staff:message", args=[thread.pk])).status_code, 404)

    def test_unread_shows_in_the_menu_until_opened_and_a_reply_reaches_the_writer(self):
        _response, thread = self.send()
        self.assertEqual(inbox.unread(self.sales).count(), 0)  # their own message
        self.client.force_login(self.boss)
        self.assertContains(self.client.get(reverse("staff:messages")), 'aria-label="1 unread"')
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("staff:message", args=[thread.pk]), {"body": "Done, it is on the card now."})
        self.assertEqual(inbox.unread(self.boss).count(), 0)
        self.assertNotContains(self.client.get(reverse("staff:messages")), "unread")
        self.assertEqual([m.to for m in mail.outbox], [["sales@example.com"]])
        self.assertEqual(inbox.unread(self.sales).count(), 1)

    def test_the_customer_page_links_to_editing_and_messaging(self):
        self.client.force_login(self.sales)
        page = self.client.get(reverse("staff:subscriber", args=[self.customer.pk]))
        self.assertContains(page, reverse("staff:card_edit", args=[self.card.pk]))
        self.assertContains(page, f"{reverse('staff:message_new')}?card={self.card.pk}")
        self.assertContains(self.client.get(f"{reverse('staff:message_new')}?card={self.card.pk}"), "About Ama Boateng")
