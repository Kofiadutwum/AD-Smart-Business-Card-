"""Staff cards: every administrator's own card, and the company card behind the landing page's two phones."""

from django.conf import settings
from django.test import override_settings

from apps.accounts.models import User
from apps.cards.models import BusinessProduct, Card, SocialLink
from apps.cards.services import ensure_staff_card

from .test_business import BusinessBase, picture


@override_settings(STAFF_TWO_FACTOR_REQUIRED=False)
class StaffCardTests(BusinessBase):
    def staff(self, role="design", email=None, **extra):
        return User.objects.create_user(email=email or f"{role}@example.com", password="x", is_staff=True, staff_role=role, **extra)

    def boss(self):
        return User.objects.create_superuser(email="boss@example.com", password="x")

    def card_post(self, url, **fields):
        data = {
            "form": "card", "full_name": "Ama Mensah", "job_title": "Designer", "business_name": "AD Graphics",
            "bio": "Hello", "whatsapp": "", "email": "ama@example.com",
            "phones-TOTAL_FORMS": "1", "phones-INITIAL_FORMS": "0", "phones-MIN_NUM_FORMS": "0",
            "phones-MAX_NUM_FORMS": "5", "phones-0-number": "024 412 3456", "phones-0-label": "Office",
        }
        data.update(fields)
        return self.client.post(url, data)

    # ------------------------------------------------------------ personal staff cards
    def test_every_administrator_gets_a_live_card_with_no_plan(self):
        member = self.staff()
        card = Card.objects.get(owner=member)
        self.assertTrue(card.is_live)
        self.assertEqual(card.website, settings.SITE_URL)
        self.assertEqual(self.client.get(f"/c/{card.slug}").status_code, 200)

    def test_the_card_goes_inactive_when_the_person_is_deactivated_and_returns_when_reactivated(self):
        member = self.staff()
        card = Card.objects.get(owner=member)
        member.is_active = False
        member.save()
        card.refresh_from_db()
        self.assertEqual(card.public_state(), Card.STATE_INACTIVE)
        member.is_active = True
        member.save()
        card.refresh_from_db()
        self.assertTrue(card.is_live)

    def test_the_website_link_is_always_there_and_cannot_be_hidden(self):
        card = Card.objects.get(owner=self.staff())
        Card.objects.filter(pk=card.pk).update(website="", hidden_fields=["website", "bio"])
        ensure_staff_card(card.owner)
        card.refresh_from_db()
        self.assertEqual(card.website, settings.SITE_URL)
        self.assertEqual(card.hidden_fields, ["bio"])

    def test_my_card_edits_the_persons_own_card_and_its_links(self):
        member = self.staff()
        self.client.force_login(member)
        self.assertEqual(self.client.get("/staff/my-card/").status_code, 200)
        response = self.card_post("/staff/my-card/")
        self.assertEqual(response.status_code, 302)
        card = Card.objects.get(owner=member)
        self.assertEqual((card.full_name, card.job_title), ("Ama Mensah", "Designer"))
        self.assertEqual(card.phones.get().number, "+233244123456")
        self.client.post("/staff/my-card/", {"form": "social", "platform": "instagram", "url": "https://instagram.com/ama"})
        link = card.social_links.get()
        self.assertContains(self.client.get(f"/c/{card.slug}"), "https://instagram.com/ama")
        self.client.post(f"/staff/card-links/{link.pk}/delete/")
        self.assertFalse(card.social_links.exists())

    def test_customers_cannot_open_the_staff_card_pages(self):
        self.client.force_login(self.make_user())
        self.assertEqual(self.client.get("/staff/my-card/").status_code, 404)
        self.assertEqual(self.client.get("/staff/company-card/").status_code, 404)

    def test_nobody_can_change_a_colleagues_links(self):
        other = self.staff("ops")
        link = SocialLink.objects.create(card=Card.objects.get(owner=other), platform="instagram", url="https://instagram.com/o")
        self.client.force_login(self.staff("support"))
        self.assertEqual(self.client.post(f"/staff/card-links/{link.pk}/delete/").status_code, 404)
        self.assertTrue(SocialLink.objects.filter(pk=link.pk).exists())

    # ------------------------------------------------------------ the company card
    def test_the_company_card_is_made_on_first_visit_and_is_public(self):
        boss = self.boss()
        self.client.force_login(boss)
        self.assertEqual(self.client.get("/staff/company-card/").status_code, 200)
        company = Card.objects.get(is_company=True)
        self.assertEqual((company.owner, company.slug), (boss, "adgraphics"))
        self.assertEqual(self.client.get("/c/adgraphics").status_code, 200)
        page = self.client.get("/b/adgraphics")
        self.assertContains(page, "Logos and brand identity")
        personal = Card.objects.get(owner=boss, is_company=False)
        self.assertEqual(self.client.get(f"/b/{personal.slug}").status_code, 404)  # the personal card has no page

    def test_super_admin_and_design_manager_may_edit_it_and_support_may_not(self):
        boss = self.boss()
        for role, status in (("design", 200), ("support", 403)):
            self.client.force_login(self.staff(role))
            self.assertEqual(self.client.get("/staff/company-card/").status_code, status, role)
        self.client.force_login(boss)
        self.assertEqual(self.client.get("/staff/company-card/").status_code, 200)

    def test_the_landing_page_phones_follow_the_company_card(self):
        self.client.force_login(self.boss())
        self.client.get("/staff/company-card/")
        home = self.client.get("/")
        self.assertContains(home, '<p class="pcard__name">AD Graphics</p>')
        self.card_post("/staff/company-card/", full_name="Ama Studio")
        self.assertContains(self.client.get("/"), '<p class="pcard__name">Ama Studio</p>')
        self.client.post("/staff/company-card/", {"form": "page", "tagline": "We print gold cards", "about": "", "hours": ""})
        self.assertContains(self.client.get("/demo/business"), "We print gold cards")

    def test_products_added_to_the_company_page_show_on_the_second_phone_and_can_be_removed(self):
        self.client.force_login(self.boss())
        self.client.get("/staff/company-card/")
        added = self.client.post(
            "/staff/company-card/products/add/",
            {"image": picture(), "title": "Gold cards", "price": "GHS 50", "description": "Heavy stock"},
        )
        self.assertEqual(added.status_code, 302)
        product = BusinessProduct.objects.get(title="Gold cards")
        for url in ("/demo/business", "/b/adgraphics"):
            self.assertContains(self.client.get(url), "Gold cards")
        self.client.post(f"/staff/company-card/products/{product.pk}/delete/")
        self.assertNotContains(self.client.get("/demo/business"), "Gold cards")

    def test_the_landing_page_keeps_the_fixed_sample_until_the_company_card_exists(self):
        self.boss()
        self.assertContains(self.client.get("/"), "Kofi Adutwum")
        self.assertEqual(self.client.get("/demo/business").status_code, 200)
