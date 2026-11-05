from apps.cards.models import Lead
from apps.support.models import SupportRequest

from .helpers import BaseTest

LEAD = {"name": "Yaw", "phone": "0244123456", "email": "yaw@example.com", "note": "Hello"}
TICKET = {
    "name": "Yaw", "email": "yaw@example.com", "category": "account", "subject": "Help", "message": "I need a hand.",
}


class PrivacyBoxTests(BaseTest):
    """Every public form that collects someone's details needs the privacy box ticked."""

    def card_url(self):
        user = self.make_user()
        card = self.make_card(user)
        self.subscribe(user, self.pro)
        return f"/c/{card.slug}"

    def test_a_card_visitor_must_tick_the_box_to_share_details(self):
        url = self.card_url() + "/connect"
        refused = self.client.post(url, LEAD)
        self.assertContains(refused, "Please tick the box to agree before sending your details.", status_code=400)
        self.assertFalse(Lead.objects.exists())
        self.client.post(url, {**LEAD, "consent": "1"})
        self.assertEqual(Lead.objects.count(), 1)

    def test_a_support_message_needs_the_box_ticked(self):
        refused = self.client.post("/contact", TICKET)
        self.assertContains(refused, "Please tick the box to agree before sending your message.")
        self.assertFalse(SupportRequest.objects.exists())
        self.client.post("/contact", {**TICKET, "consent": "1"})
        self.assertEqual(SupportRequest.objects.count(), 1)

    def test_the_forms_link_to_the_privacy_policy(self):
        for url in ("/contact", self.card_url()):
            self.assertContains(self.client.get(url), "Read our Privacy Policy")
