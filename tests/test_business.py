"""The business page: product pictures squeezed to 50 KB, plan limits, the card button, enquiries."""

import io
import tempfile

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image

from apps.cards.models import (
    MAX_REVIEWS, MAX_SERVICES, PRODUCT_IMAGE_BYTES, BusinessPage, BusinessProduct, BusinessReview, BusinessService, Lead,
)
from apps.core.models import SiteSettings
from apps.core.utils import process_image

from .helpers import BaseTest

LOCAL_FILES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def picture(size=(900, 700), name="photo.png"):
    """A picture of pure noise: the worst case for compression."""
    out = io.BytesIO()
    Image.effect_noise(size, 90).convert("RGB").save(out, format="PNG")
    return SimpleUploadedFile(name, out.getvalue(), content_type="image/png")


class BusinessBase(BaseTest):
    """Files go to a throwaway folder, never the real media folder or Cloudinary."""

    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.media = folder.name
        override = override_settings(MEDIA_ROOT=folder.name, STORAGES=LOCAL_FILES)
        override.enable()
        self.addCleanup(override.disable)

    def owner(self, plan=None, on=True, **page):
        user = self.make_user()
        self.card = self.make_card(user, business_name="Ama Fabrics")
        self.subscribe(user, plan or self.pro)
        self.card.refresh_from_db()  # subscribing activated it; saving the stale copy would put it back to draft
        self.page = BusinessPage.objects.create(owner=user, is_on=on, **page)
        return user

    def add_products(self, count, prefix="Item"):
        BusinessProduct.objects.bulk_create(
            BusinessProduct(page=self.page, image=f"products/{prefix}{n}.webp", title=f"{prefix} {n}", position=n)
            for n in range(count)
        )


class ImageSquashTests(BusinessBase):
    def test_a_big_noisy_picture_ends_up_at_50_kb_or_less(self):
        upload = picture((1400, 1000))
        self.assertGreater(upload.size, 500_000)
        result = process_image(upload, folder="products", max_px=1000, target_bytes=PRODUCT_IMAGE_BYTES)
        self.assertLessEqual(result.size, 50 * 1024)
        image = Image.open(io.BytesIO(result.read()))
        self.assertEqual(image.format, "WEBP")
        self.assertLessEqual(max(image.size), 1000)

    def test_a_small_plain_picture_is_left_sharp(self):
        out = io.BytesIO()
        Image.new("RGB", (400, 300), "#0048A9").save(out, format="PNG")
        result = process_image(
            SimpleUploadedFile("p.png", out.getvalue(), "image/png"), folder="products", max_px=1000, target_bytes=PRODUCT_IMAGE_BYTES
        )
        self.assertLessEqual(result.size, 50 * 1024)
        self.assertEqual(Image.open(io.BytesIO(result.read())).size, (400, 300))

    def test_other_uploads_are_not_squeezed(self):
        result = process_image(picture((900, 700)), folder="avatars", max_px=640)
        self.assertGreater(result.size, 50 * 1024)

    def test_something_that_is_not_a_picture_is_refused(self):
        from django.core.exceptions import ValidationError

        fake = SimpleUploadedFile("evil.png", b"<script>alert(1)</script>", "image/png")
        with self.assertRaises(ValidationError):
            process_image(fake, folder="products", target_bytes=PRODUCT_IMAGE_BYTES)


class PlanAllowanceTests(BusinessBase):
    def test_professional_gets_ten_products_business_gets_thirty_basic_none(self):
        self.assertEqual((self.pro.allow_business_page, self.pro.max_products), (True, 10))
        self.assertEqual((self.business.allow_business_page, self.business.max_products), (True, 30))
        self.assertFalse(self.basic.allow_business_page)

    def test_pricing_lists_the_business_page(self):
        rows = dict((text, on) for on, text in self.pro.feature_rows())
        self.assertTrue(rows["Business page with up to 10 products"])
        self.assertIn("Business page with up to 30 products", [t for _, t in self.business.feature_rows()])
        self.assertFalse(dict((text, on) for on, text in self.basic.feature_rows())["Business page for your products"])

    def test_basic_sees_an_upgrade_message_and_cannot_add_anything(self):
        user = self.make_user()
        self.make_card(user)
        self.subscribe(user, self.basic)
        self.client.force_login(user)
        page = self.client.get("/dashboard/business/")
        self.assertContains(page, "Business pages come with Professional and Business")
        self.assertEqual(self.client.get("/dashboard/business/products/add/").status_code, 404)
        self.assertFalse(BusinessPage.objects.exists())

    def test_the_menu_has_a_business_page_link(self):
        self.client.force_login(self.owner())
        self.assertContains(self.client.get("/dashboard/"), 'href="/dashboard/business/"')


class DashboardTests(BusinessBase):
    def setUp(self):
        super().setUp()
        self.user = self.owner(on=False)
        self.client.force_login(self.user)

    def add_product(self, **data):
        fields = {"title": "Kente stole", "price": "GHS 120", "description": "Handwoven.", "image": picture()}
        fields.update(data)
        return self.client.post("/dashboard/business/products/add/", fields)

    def test_a_product_picture_is_saved_at_50_kb_or_less(self):
        response = self.add_product()
        self.assertRedirects(response, "/dashboard/business/", fetch_redirect_response=False)
        product = BusinessProduct.objects.get()
        self.assertEqual((product.title, product.price), ("Kente stole", "GHS 120"))
        self.assertLessEqual(product.image.size, 50 * 1024)
        self.assertTrue(product.image.name.endswith(".webp"))

    def test_a_picture_is_needed_for_a_new_product(self):
        response = self.add_product(image="")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(BusinessProduct.objects.exists())

    def test_a_file_that_is_not_a_picture_is_refused(self):
        response = self.add_product(image=SimpleUploadedFile("x.png", b"not an image", "image/png"))
        self.assertContains(response, "Upload a valid image.")
        self.assertFalse(BusinessProduct.objects.exists())

    def test_price_and_description_are_optional(self):
        self.add_product(price="", description="")
        self.assertEqual(BusinessProduct.objects.get().price, "")

    def test_professional_stops_at_ten_products(self):
        self.add_products(10)
        response = self.add_product(title="One too many")
        self.assertRedirects(response, "/dashboard/business/", fetch_redirect_response=False)
        self.assertEqual(BusinessProduct.objects.count(), 10)
        self.assertContains(self.client.get("/dashboard/business/"), "You have used all 10 places here.")

    def test_business_stops_at_thirty_products(self):
        subscription = self.user.subscription
        subscription.plan = self.business
        subscription.save()
        self.add_products(29)
        self.add_product(title="Number thirty")
        self.assertEqual(BusinessProduct.objects.count(), 30)
        self.add_product(title="Number thirty-one")
        self.assertEqual(BusinessProduct.objects.count(), 30)

    def test_editing_without_a_new_picture_keeps_the_old_one_and_a_new_one_replaces_it(self):
        self.add_product()
        product = BusinessProduct.objects.get()
        old = product.image.name
        url = f"/dashboard/business/products/{product.pk}/edit/"
        self.client.post(url, {"title": "Kente scarf", "price": "GHS 90", "description": ""})
        product.refresh_from_db()
        self.assertEqual((product.title, product.image.name), ("Kente scarf", old))
        self.client.post(url, {"title": "Kente scarf", "price": "GHS 90", "description": "", "image": picture((600, 600))})
        product.refresh_from_db()
        self.assertNotEqual(product.image.name, old)
        self.assertFalse(product.image.storage.exists(old))
        self.assertLessEqual(product.image.size, 50 * 1024)

    def test_removing_a_product_deletes_its_picture(self):
        self.add_product()
        product = BusinessProduct.objects.get()
        name = product.image.name
        self.client.post(f"/dashboard/business/products/{product.pk}/delete/")
        self.assertFalse(BusinessProduct.objects.exists())
        self.assertFalse(product.image.storage.exists(name))

    def test_services_and_reviews(self):
        self.client.post("/dashboard/business/services/add/", {"title": "Tailoring", "description": "Made to measure."})
        self.client.post("/dashboard/business/reviews/add/", {"author": "Efua", "text": "Lovely cloth.", "rating": "5"})
        self.client.post("/dashboard/business/reviews/add/", {"author": "Kojo", "text": "Quick delivery.", "rating": ""})
        self.assertEqual(BusinessService.objects.get().title, "Tailoring")
        self.assertEqual(list(BusinessReview.objects.values_list("author", "rating")), [("Efua", 5), ("Kojo", None)])
        self.assertEqual(BusinessReview.objects.first().stars, "★★★★★")
        self.assertEqual(BusinessReview.objects.last().stars, "☆☆☆☆☆")

    def test_services_and_reviews_have_a_ceiling_too(self):
        BusinessService.objects.bulk_create(BusinessService(page=self.page, title=f"S{n}") for n in range(MAX_SERVICES))
        BusinessReview.objects.bulk_create(BusinessReview(page=self.page, author="A", text="T") for _ in range(MAX_REVIEWS))
        self.client.post("/dashboard/business/services/add/", {"title": "One more"})
        self.client.post("/dashboard/business/reviews/add/", {"author": "B", "text": "One more"})
        self.assertEqual(BusinessService.objects.count(), MAX_SERVICES)
        self.assertEqual(BusinessReview.objects.count(), MAX_REVIEWS)

    def test_the_page_details_save_and_the_switch_works(self):
        response = self.client.post("/dashboard/business/", {
            "is_on": "on", "tagline": "Cloth from Cape Coast", "about": "We weave.", "hours": "Mon–Fri 8am–6pm",
        })
        self.assertRedirects(response, "/dashboard/business/", fetch_redirect_response=False)
        self.page.refresh_from_db()
        self.assertTrue(self.page.is_on)
        self.assertEqual(self.page.tagline, "Cloth from Cape Coast")

    def test_nobody_can_touch_another_customers_items(self):
        self.add_product()
        product = BusinessProduct.objects.get()
        other = self.make_user("other@example.com")
        self.make_card(other)
        self.subscribe(other, self.pro)
        self.client.force_login(other)
        self.assertEqual(self.client.get(f"/dashboard/business/products/{product.pk}/edit/").status_code, 404)
        self.assertEqual(self.client.post(f"/dashboard/business/products/{product.pk}/delete/").status_code, 404)
        self.assertEqual(self.client.get("/dashboard/business/nonsense/add/").status_code, 404)
        self.assertTrue(BusinessProduct.objects.filter(pk=product.pk).exists())


class PublicPageTests(BusinessBase):
    def test_a_page_that_is_off_is_not_found_for_visitors_but_the_owner_can_preview_it(self):
        user = self.owner(on=False, tagline="Cloth from Cape Coast")
        self.assertEqual(self.client.get(f"/b/{self.card.slug}").status_code, 404)
        self.client.force_login(user)
        page = self.client.get(f"/b/{self.card.slug}")
        self.assertContains(page, "Only you can see this page.")
        self.assertContains(page, "Cloth from Cape Coast")

    def test_everything_the_owner_wrote_shows_up(self):
        self.owner(tagline="Cloth from Cape Coast", about="We weave kente.", hours="Mon–Fri 8am–6pm")
        BusinessProduct.objects.create(page=self.page, image="products/a.webp", title="Kente stole", price="GHS 120", description="Handwoven.")
        BusinessService.objects.create(page=self.page, title="Tailoring", description="Made to measure.")
        BusinessReview.objects.create(page=self.page, author="Efua", text="Lovely cloth.", rating=4)
        page = self.client.get(f"/b/{self.card.slug}")
        for text in ("Ama Fabrics", "Cloth from Cape Coast", "We weave kente.", "Mon–Fri 8am–6pm", "Kente stole", "GHS 120",
                     "Handwoven.", "Tailoring", "Made to measure.", "Lovely cloth.", "Efua", "★★★★☆", "4 out of 5 stars"):
            self.assertContains(page, text)
        self.assertContains(page, "/media/products/a.webp")
        self.assertContains(page, "Send an enquiry")

    def test_empty_sections_are_left_out(self):
        self.owner()
        page = self.client.get(f"/b/{self.card.slug}")
        for heading in ("About us", "Products", "Services", "What customers say", "Opening hours"):
            self.assertNotContains(page, f">{heading}<")

    def test_only_as_many_products_as_the_plan_allows_are_shown(self):
        self.owner()
        self.add_products(12)
        page = self.client.get(f"/b/{self.card.slug}")
        self.assertContains(page, "Item 9<")
        self.assertNotContains(page, "Item 10<")

    def test_the_logo_comes_from_the_card_and_falls_back_to_the_photo(self):
        self.owner()
        self.card.avatar = "avatars/me.webp"
        self.card.save()
        self.assertContains(self.client.get(f"/b/{self.card.slug}"), "/media/avatars/me.webp")
        self.card.logo = "logos/ama.webp"
        self.card.save()
        page = self.client.get(f"/b/{self.card.slug}")
        self.assertContains(page, "/media/logos/ama.webp")
        self.assertNotContains(page, "/media/avatars/me.webp")

    def test_a_plan_without_business_pages_shows_nothing(self):
        self.owner()
        sub = self.card.owner.subscription
        sub.plan = self.basic
        sub.save()
        self.assertEqual(self.client.get(f"/b/{self.card.slug}").status_code, 404)

    def test_an_expired_plan_shows_the_inactive_notice(self):
        from datetime import timedelta

        from django.utils import timezone

        self.owner()
        sub = self.card.owner.subscription
        sub.expires_at = timezone.now() - timedelta(days=90)  # well past the grace period
        sub.save()
        self.assertContains(self.client.get(f"/b/{self.card.slug}"), "currently inactive")

    def test_an_old_link_redirects_to_the_new_one(self):
        from apps.cards.services import change_slug

        self.owner()
        old = self.card.slug
        change_slug(self.card, "ama-fabrics")
        response = self.client.get(f"/b/{old}")
        self.assertRedirects(response, "/b/ama-fabrics", status_code=301, fetch_redirect_response=False)

    def test_a_card_nobody_has_set_up_has_no_business_page(self):
        from apps.cards.models import Card

        card = Card.objects.create(owner=None, slug="zz9plural", full_name="")
        self.assertEqual(self.client.get(f"/b/{card.slug}").status_code, 404)


class CardButtonTests(BusinessBase):
    def test_explore_business_appears_on_the_card_when_the_page_is_on(self):
        self.owner()
        page = self.client.get(f"/c/{self.card.slug}")
        self.assertContains(page, "Explore Business")
        self.assertContains(page, f'href="/b/{self.card.slug}"')

    def test_no_button_while_the_page_is_off_or_the_plan_has_none(self):
        self.owner(on=False)
        self.assertNotContains(self.client.get(f"/c/{self.card.slug}"), "Explore Business")
        self.page.is_on = True
        self.page.save()
        sub = self.card.owner.subscription
        sub.plan = self.basic
        sub.save()
        self.assertNotContains(self.client.get(f"/c/{self.card.slug}"), "Explore Business")

    def test_every_card_on_a_team_plan_points_to_the_one_company_page(self):
        from apps.cards.services import create_card

        user = self.owner(plan=self.business)
        second = create_card(user, "Kofi Boateng")
        for card in (self.card, second):
            self.assertContains(self.client.get(f"/c/{card.slug}"), f'href="/b/{self.card.slug}"')
        self.assertEqual(self.client.get(f"/b/{second.slug}").status_code, 200)

    def test_the_shared_card_still_carries_its_contact_details_after_the_split(self):
        self.owner()
        self.card.email = "ama@example.com"
        self.card.whatsapp = "+233244123456"
        self.card.save()
        for url in (f"/c/{self.card.slug}", f"/b/{self.card.slug}"):
            page = self.client.get(url)
            self.assertContains(page, "mailto:ama@example.com")
            self.assertContains(page, "https://wa.me/233244123456")


class EnquiryTests(BusinessBase):
    def setUp(self):
        super().setUp()
        self.owner()
        self.url = f"/b/{self.card.slug}/enquire"
        self.data = {"name": "Yaw", "phone": "0244123456", "email": "yaw@example.com", "note": "Do you ship to Kumasi?"}

    def test_an_enquiry_reaches_the_owner_by_email_and_in_their_contacts(self):
        response = self.client.post(self.url, self.data)
        self.assertRedirects(response, f"/b/{self.card.slug}", fetch_redirect_response=False)
        lead = Lead.objects.get()
        self.assertEqual((lead.source, lead.card_id, lead.note), ("business", self.card.pk, "Do you ship to Kumasi?"))
        self.assertEqual(mail.outbox[-1].to, ["kwame@example.com"])
        self.assertEqual(mail.outbox[-1].subject, "New enquiry from Yaw")
        self.assertIn("Do you ship to Kumasi?", mail.outbox[-1].body)
        self.client.force_login(self.card.owner)
        contacts = self.client.get("/dashboard/contacts/")
        self.assertContains(contacts, "Yaw")
        self.assertContains(contacts, "BUSINESS")

    def test_the_visitor_is_thanked_by_business_name(self):
        page = self.client.post(self.url, self.data, follow=True)
        self.assertContains(page, "Thank you. Ama Fabrics has your enquiry.")

    def test_a_message_and_a_way_to_reply_are_needed(self):
        for bad in ({**self.data, "note": ""}, {**self.data, "phone": "", "email": ""}):
            response = self.client.post(self.url, bad)
            self.assertEqual(response.status_code, 400)
        self.assertFalse(Lead.objects.exists())

    def test_robots_get_a_thank_you_and_nothing_is_saved(self):
        response = self.client.post(self.url, {**self.data, "website_url": "http://spam.example"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Lead.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_the_same_visitor_can_send_three_an_hour(self):
        for _ in range(5):
            self.client.post(self.url, self.data)
        self.assertEqual(Lead.objects.count(), 3)

    def test_enquiries_can_be_switched_off_for_the_whole_site(self):
        site = SiteSettings.load()
        site.leads_enabled = False
        site.save()
        self.assertNotContains(self.client.get(f"/b/{self.card.slug}"), "Send an enquiry")
        self.client.post(self.url, self.data)
        self.assertFalse(Lead.objects.exists())

    def test_the_form_is_not_open_while_the_page_is_off(self):
        self.page.is_on = False
        self.page.save()
        self.assertEqual(self.client.post(self.url, self.data).status_code, 404)
        self.assertFalse(Lead.objects.exists())

    def test_only_post_is_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
