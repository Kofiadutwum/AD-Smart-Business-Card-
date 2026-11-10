"""The shop marketplace: WhatsApp buy links, tracking, listing rules, ranking, search, the landing slider,
and what owners and staff can do."""

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.cards import marketplace
from apps.cards.models import BusinessEvent, BusinessPage, BusinessProduct, ProductCategory, SocialLink
from apps.core.models import AuditLog

from .test_business import BusinessBase, picture

AGENT = {"HTTP_USER_AGENT": "Mozilla/5.0 (test phone)"}


class MarketBase(BusinessBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def shop(self, email, name, products=("Red dress",), whatsapp="+233244123456", plan=None, **page):
        user = self.make_user(email=email, full_name=name)
        card = self.make_card(user, business_name=name)
        self.subscribe(user, plan or self.pro)
        card.refresh_from_db()  # subscribing activated it; saving the stale copy would put it back to draft
        card.whatsapp = whatsapp
        card.save(update_fields=["whatsapp"])
        shop = BusinessPage.objects.create(owner=user, is_on=True, **page)
        items = [
            BusinessProduct.objects.create(page=shop, image=f"products/{n}-{email}.webp", title=title, price="GHS 120", position=n)
            for n, title in enumerate(products)
        ]
        return shop, card, items

    def see(self, shop, product, kind=BusinessEvent.PRODUCT, times=1):
        for n in range(times):
            BusinessEvent.objects.create(page=shop, product=product, kind=kind, visitor_hash=f"visitor-{n}")
        marketplace.clear()

    def staff_client(self, role):
        client = Client()
        User.objects.create_user(email=f"{role}@example.com", password="Staff-pass-2026", is_staff=True, staff_role=role)
        client.post("/staff/login", {"email": f"{role}@example.com", "password": "Staff-pass-2026"})
        return client


class BuyLinkTests(MarketBase):
    def test_the_cards_whatsapp_number_makes_the_chat_link_with_the_message_typed(self):
        shop, card, _ = self.shop("ama@example.com", "Ama Fabrics")
        page = self.client.get(f"/b/{card.slug}")
        self.assertContains(page, "<span>Buy</span>")
        self.assertContains(page, 'href="https://wa.me/233244123456?text=')
        for typed in ("Red%20dress", "GHS%20120", "Ama%20Fabrics"):
            self.assertContains(page, typed)

    def test_a_pasted_whatsapp_link_is_used_only_when_there_is_no_number(self):
        shop, card, _ = self.shop("ama@example.com", "Ama Fabrics", whatsapp="")
        SocialLink.objects.create(card=card, platform="whatsapp", url="https://wa.me/233201112222")
        self.assertContains(self.client.get(f"/b/{card.slug}"), 'href="https://wa.me/233201112222?text=')
        card.whatsapp = "+233244123456"
        card.save(update_fields=["whatsapp"])
        page = self.client.get(f"/b/{card.slug}")
        self.assertContains(page, 'href="https://wa.me/233244123456?text=')
        self.assertNotContains(page, "233201112222?text=")

    def test_links_other_than_a_chat_are_opened_as_written_and_an_old_message_is_replaced(self):
        self.assertEqual(marketplace.buy_url({"url": "https://chat.whatsapp.com/ABC"}, "hi"), "https://chat.whatsapp.com/ABC")
        link = marketplace.buy_url({"url": "https://api.whatsapp.com/send?phone=233244123456&text=old"}, "new one")
        self.assertEqual(parse_qs(urlsplit(link).query), {"phone": ["233244123456"], "text": ["new one"]})
        self.assertEqual(marketplace.buy_url(None, "hi"), "")

    def test_no_number_and_no_link_means_no_buy_button(self):
        shop, card, _ = self.shop("ama@example.com", "Ama Fabrics", whatsapp="")
        self.assertNotContains(self.client.get(f"/b/{card.slug}"), "<span>Buy</span>")

    def test_a_number_hidden_on_the_card_still_gives_a_buy_button(self):
        shop, card, _ = self.shop("ama@example.com", "Ama Fabrics")
        card.hidden_fields = ["whatsapp"]
        card.save(update_fields=["hidden_fields"])
        self.assertContains(self.client.get(f"/b/{card.slug}"), 'href="https://wa.me/233244123456?text=')


class StorefrontTests(MarketBase):
    """The business page as a shop front: search, category chips, the big product view and the order cart."""

    def test_the_page_carries_the_search_the_big_view_and_the_cart_for_a_shop_with_a_whatsapp_number(self):
        shop, card, items = self.shop("ama@example.com", "Ama Fabrics", products=("Red dress", "Blue scarf"))
        page = self.client.get(f"/b/{card.slug}")
        self.assertContains(page, "data-tools")
        self.assertContains(page, "<dialog")
        self.assertContains(page, 'data-cart-url="https://wa.me/233244123456?text="')
        self.assertContains(page, 'data-cart-intro="Hi Ama Fabrics, I would like to order:"')
        self.assertContains(page, "data-cart-slot", count=2)
        self.assertContains(page, f'data-title="Blue scarf" data-price="GHS 120" data-cat="other" data-search="Blue scarf ')

    def test_no_chat_number_means_no_cart_and_so_does_a_link_that_cannot_carry_a_message(self):
        shop, card, _ = self.shop("ama@example.com", "Ama Fabrics", whatsapp="")
        self.assertNotContains(self.client.get(f"/b/{card.slug}"), "data-cart")
        SocialLink.objects.create(card=card, platform="whatsapp", url="https://chat.whatsapp.com/ABC")
        page = self.client.get(f"/b/{card.slug}")
        self.assertContains(page, "biz-buy")  # Buy still opens the group as the owner wrote it
        self.assertNotContains(page, "data-cart")

    def test_category_chips_need_two_groups_and_name_the_ones_without_a_category_other(self):
        shop, card, items = self.shop("ama@example.com", "Ama Fabrics", products=("Red dress", "Blue scarf"))
        self.assertNotContains(self.client.get(f"/b/{card.slug}"), 'class="shop-chip"')
        category, _ = ProductCategory.objects.get_or_create(slug="fashion", defaults={"name": "Fashion and clothing"})
        items[0].category = category
        items[0].save()
        page = self.client.get(f"/b/{card.slug}")
        self.assertContains(page, 'class="shop-chip"', count=3)  # All, the category, Other
        self.assertContains(page, 'data-cat="fashion"')
        self.assertContains(page, 'data-cat="other"')

    def test_a_link_with_a_product_in_it_marks_that_product_and_the_sample_never_does(self):
        shop, card, items = self.shop("ama@example.com", "Ama Fabrics", products=("Red dress", "Blue scarf"))
        page = self.client.get(f"/b/{card.slug}?p={items[1].pk}")
        self.assertContains(page, f'class="biz-item biz-item--picked" id="p-{items[1].pk}"')
        self.assertContains(page, "biz-item--picked", count=1)
        sample = self.client.get("/demo/business")
        self.assertNotContains(sample, "biz-item--picked")
        self.assertNotContains(sample, "data-cart")

    def test_contact_details_sit_under_their_own_heading_and_the_quick_buttons_stay_at_the_top(self):
        shop, card, _ = self.shop("ama@example.com", "Ama Fabrics")
        page = self.client.get(f"/b/{card.slug}")
        self.assertContains(page, ">Contact<", count=1)
        self.assertContains(page, 'aria-label="Contact details"', count=1)
        self.assertContains(page, 'class="pcard__quick"', count=1)


class TrackingTests(MarketBase):
    def setUp(self):
        super().setUp()
        self.shop_page, self.card, (self.dress,) = self.shop("ama@example.com", "Ama Fabrics")

    def kinds(self):
        return sorted(BusinessEvent.objects.values_list("kind", flat=True))

    def test_arriving_on_a_product_counts_a_visit_and_an_opened_product_once_a_day(self):
        for _ in range(2):
            self.client.get(f"/b/{self.card.slug}?p={self.dress.pk}", **AGENT)
        self.assertEqual(self.kinds(), ["product", "view"])
        self.assertEqual(BusinessEvent.objects.get(kind="product").product, self.dress)

    def test_the_owner_bots_and_unknown_browsers_are_not_counted(self):
        self.client.get(f"/b/{self.card.slug}?p={self.dress.pk}", HTTP_USER_AGENT="Googlebot/2.1")
        self.client.get(f"/b/{self.card.slug}?p={self.dress.pk}")
        self.client.force_login(self.shop_page.owner)
        self.client.get(f"/b/{self.card.slug}?p={self.dress.pk}", **AGENT)
        self.assertEqual(BusinessEvent.objects.count(), 0)

    def test_the_beacon_counts_a_buy_tap_once_a_day(self):
        url = f"/b/{self.card.slug}/e"
        for _ in range(2):
            response = self.client.post(url, {"kind": "buy", "product": self.dress.pk}, content_type="application/json", **AGENT)
            self.assertEqual(response.status_code, 204)
        self.assertEqual(self.kinds(), ["buy"])

    def test_the_beacon_ignores_other_shops_products_hidden_products_and_other_kinds(self):
        other, _, (theirs,) = self.shop("kofi@example.com", "Kofi Shoes", products=("Boots",))
        url = f"/b/{self.card.slug}/e"
        post = lambda body: self.client.post(url, body, content_type="application/json", **AGENT)  # noqa: E731
        self.assertEqual(post({"kind": "buy", "product": theirs.pk}).status_code, 204)
        self.assertEqual(post({"kind": "view", "product": self.dress.pk}).status_code, 204)
        self.dress.is_hidden = True
        self.dress.save()
        self.assertEqual(post({"kind": "buy", "product": self.dress.pk}).status_code, 204)
        self.assertEqual(BusinessEvent.objects.count(), 0)
        self.assertEqual(self.client.post(url, "not json", content_type="application/json", **AGENT).status_code, 400)

    def test_an_enquiry_counts(self):
        data = {"name": "Yaw", "phone": "0244123456", "email": "yaw@example.com", "note": "Do you ship?", "consent": "1"}
        self.client.post(f"/b/{self.card.slug}/enquire", data, **AGENT)
        self.assertEqual(self.kinds(), ["enquiry"])


class MarketplaceTests(MarketBase):
    def names(self, url="/shops"):
        return [s["name"] for s in self.client.get(url).context["shops"]]

    def test_a_shop_with_products_is_listed_with_its_buy_button(self):
        self.shop("ama@example.com", "Ama Fabrics")
        page = self.client.get("/shops")
        self.assertContains(page, "Ama Fabrics")
        self.assertContains(page, "Red dress")
        self.assertContains(page, "<span>Buy</span>")
        self.assertContains(page, "does not take payment or deliver")

    def test_the_slash_address_redirects_and_keeps_the_search(self):
        response = self.client.get("/shops/?q=dress")
        self.assertRedirects(response, "/shops?q=dress", status_code=301, fetch_redirect_response=False)

    def test_who_is_left_out(self):
        off, _, _ = self.shop("off@example.com", "Page Off")
        off.is_on = False
        off.save()
        out, _, _ = self.shop("out@example.com", "Opted Out")
        out.in_marketplace = False
        out.save()
        hidden, _, _ = self.shop("hid@example.com", "Hidden Shop")
        hidden.marketplace_hidden = True
        hidden.save()
        self.shop("none@example.com", "Nothing Here", products=())
        _, _, (gone, kept) = self.shop("some@example.com", "Some Shop", products=("Gone", "Kept"))
        gone.is_hidden = True
        gone.save()
        self.shop("ok@example.com", "Ama Fabrics")
        listed = self.client.get("/shops").context["shops"]
        self.assertEqual(sorted(s["name"] for s in listed), ["Ama Fabrics", "Some Shop"])
        self.assertEqual([p["title"] for p in next(s for s in listed if s["name"] == "Some Shop")["shown"]], ["Kept"])

    def test_only_the_products_the_plan_allows_are_listed(self):
        self.shop("ama@example.com", "Ama Fabrics", products=tuple(f"Item {n}" for n in range(55)))
        shop = self.client.get("/shops").context["shops"][0]
        self.assertEqual(len(shop["products"]), 50)  # Professional: up to 50
        self.assertEqual(len(shop["shown"]), marketplace.SHOP_PREVIEW)

    def test_busier_shops_come_first(self):
        quiet, _, (q_item,) = self.shop("q@example.com", "Quiet Shop")
        busy, _, (b_item,) = self.shop("b@example.com", "Busy Shop")
        self.see(quiet, q_item, BusinessEvent.PRODUCT, times=2)  # worth 2
        self.see(busy, b_item, BusinessEvent.BUY, times=1)  # worth 3
        self.assertEqual(self.names(), ["Busy Shop", "Quiet Shop"])

    def test_events_older_than_30_days_do_not_count(self):
        old, _, (o_item,) = self.shop("a@example.com", "Aaa Old")
        new, _, (n_item,) = self.shop("b@example.com", "Bbb New")
        self.see(old, o_item, BusinessEvent.BUY, times=5)
        BusinessEvent.objects.update(occurred_at=timezone.now() - timedelta(days=31))
        self.see(new, n_item, BusinessEvent.PRODUCT, times=1)
        self.assertEqual(self.names(), ["Bbb New", "Aaa Old"])

    def test_search_forgives_a_slip_of_the_finger(self):
        self.shop("ama@example.com", "Ama Fabrics", products=("Red dress", "Blue shoes"))
        self.shop("kofi@example.com", "Kofi Cars", products=("Spare tyre",))
        self.assertEqual(self.names("/shops?q=fabrcs"), ["Ama Fabrics"])  # a business, mistyped
        found = self.client.get("/shops?q=shose").context["shops"]  # a product, mistyped
        self.assertEqual([s["name"] for s in found], ["Ama Fabrics"])
        self.assertEqual([p["title"] for p in found[0]["shown"]], ["Blue shoes"])
        self.assertEqual(self.names("/shops?q=dres"), ["Ama Fabrics"])  # part of a word
        self.assertEqual(self.names("/shops?q=zzzzzz"), [])

    def test_a_category_narrows_the_list_and_the_sidebar_counts_products(self):
        shop, _, (dress, cake) = self.shop("ama@example.com", "Ama Fabrics", products=("Red dress", "Cake"))
        dress.category = ProductCategory.objects.get(slug="fashion")
        dress.save()
        page = self.client.get("/shops?c=fashion")
        self.assertEqual([p["title"] for p in page.context["shops"][0]["shown"]], ["Red dress"])
        self.assertEqual(self.client.get("/shops?c=food-drinks").context["shops"], [])
        slugs = {c["slug"]: c["count"] for c in self.client.get("/shops").context["categories"]}
        self.assertEqual(slugs, {"fashion": 1, "other": 1})

    def test_an_empty_marketplace_says_so(self):
        self.assertContains(self.client.get("/shops"), "No shops are listed yet.")


class SliderTests(MarketBase):
    def viewed(self, count):
        shop, _, items = self.shop("ama@example.com", "Ama Fabrics", products=tuple(f"Thing {n}" for n in range(4)))
        for item in items[:count]:
            self.see(shop, item, BusinessEvent.PRODUCT, times=1)

    def test_with_fewer_than_three_viewed_products_there_is_no_shop_window(self):
        self.viewed(2)
        self.assertNotContains(self.client.get("/"), 'id="shop-window"')

    def test_the_most_viewed_products_fill_the_window_and_the_run_is_repeated_to_fill_the_screen(self):
        self.viewed(3)
        response = self.client.get("/")
        self.assertContains(response, 'id="shop-window"')
        self.assertContains(response, "View businesses")
        slides = response.context["market_slides"]
        self.assertEqual(len(slides), 9)  # three products, repeated until a run is wider than 1500 px
        self.assertEqual({s["title"] for s in slides}, {"Thing 0", "Thing 1", "Thing 2"})  # the unviewed one is left out
        self.assertEqual([s["dup"] for s in slides], [False] * 3 + [True] * 6)

    def test_a_hidden_shop_leaves_the_window_at_once(self):
        self.viewed(3)
        shop = BusinessPage.objects.get()
        shop.marketplace_hidden = True
        shop.save()
        self.assertNotContains(self.client.get("/"), 'id="shop-window"')


class OwnerTests(MarketBase):
    def setUp(self):
        super().setUp()
        self.page, self.card, _ = self.shop("ama@example.com", "Ama Fabrics")
        self.client.force_login(self.page.owner)

    def test_the_dashboard_shows_the_shop_is_listed_with_30_day_numbers(self):
        product = self.page.products.get()
        self.see(self.page, product, BusinessEvent.PRODUCT, times=3)
        self.see(self.page, product, BusinessEvent.BUY, times=2)
        page = self.client.get("/dashboard/business/")
        self.assertContains(page, "Listed in the marketplace")
        self.assertContains(page, "3 opened, 2 WhatsApp taps in 30 days")

    def test_an_owner_can_leave_and_rejoin(self):
        self.client.post("/dashboard/business/", {"is_on": "on", "tagline": "", "about": "", "hours": ""})
        self.page.refresh_from_db()
        self.assertFalse(self.page.in_marketplace)
        self.assertEqual(self.client.get("/shops").context["shops"], [])
        self.client.post("/dashboard/business/", {"is_on": "on", "in_marketplace": "on", "tagline": "", "about": "", "hours": ""})
        self.assertEqual(len(self.client.get("/shops").context["shops"]), 1)

    def test_a_product_takes_an_active_category_only(self):
        fashion = ProductCategory.objects.get(slug="fashion")
        add = lambda cat: self.client.post("/dashboard/business/products/add/", {  # noqa: E731
            "title": "Kente stole", "price": "GHS 120", "category": cat.pk, "image": picture()})
        add(fashion)
        self.assertEqual(BusinessProduct.objects.get(title="Kente stole").category, fashion)
        fashion.is_active = False
        fashion.save()
        add(fashion)
        self.assertEqual(BusinessProduct.objects.filter(title="Kente stole").count(), 1)


class StaffTests(MarketBase):
    def setUp(self):
        super().setUp()
        self.page, self.card, (self.dress,) = self.shop("ama@example.com", "Ama Fabrics")

    def test_support_hides_a_shop_and_it_is_gone_at_once_with_the_reason_logged(self):
        client = self.staff_client("support")
        self.assertContains(client.get("/staff/marketplace/"), "Ama Fabrics")
        self.assertEqual(len(self.client.get("/shops").context["shops"]), 1)
        client.post(f"/staff/marketplace/{self.page.pk}/hide/", {"reason": "Sells fakes"})
        self.assertEqual(self.client.get("/shops").context["shops"], [])
        self.assertEqual(AuditLog.objects.get(action="shop_hidden").reason, "Sells fakes")
        self.assertEqual(self.client.get(f"/b/{self.card.slug}").status_code, 200)  # its own page still works
        client.post(f"/staff/marketplace/{self.page.pk}/hide/", {"reason": "Sorted out"})
        self.assertEqual(len(self.client.get("/shops").context["shops"]), 1)

    def test_a_hidden_product_leaves_the_shop_page_and_the_marketplace(self):
        client = self.staff_client("super")
        client.post(f"/staff/marketplace/products/{self.dress.pk}/hide/", {"reason": "Not allowed"})
        self.assertNotContains(self.client.get(f"/b/{self.card.slug}"), "Red dress")
        self.assertEqual(self.client.get("/shops").context["shops"], [])

    def test_a_reason_is_required(self):
        client = self.staff_client("support")
        client.post(f"/staff/marketplace/{self.page.pk}/hide/", {"reason": ""})
        self.page.refresh_from_db()
        self.assertFalse(self.page.marketplace_hidden)

    def test_only_super_and_support_may_moderate(self):
        for role in ("finance", "design", "ops"):
            client = self.staff_client(role)
            self.assertEqual(client.get("/staff/marketplace/").status_code, 403, role)
            self.assertEqual(client.post(f"/staff/marketplace/{self.page.pk}/hide/", {"reason": "x"}).status_code, 403, role)

    def test_new_categories_get_a_link_name_and_never_take_the_name_other(self):
        client = self.staff_client("support")
        client.post("/staff/marketplace/categories/", {"name": "Fresh fruit", "position": 20, "is_active": "on"})
        client.post("/staff/marketplace/categories/", {"name": "Other", "position": 21, "is_active": "on"})
        self.assertEqual(ProductCategory.objects.get(name="Fresh fruit").slug, "fresh-fruit")
        self.assertEqual(ProductCategory.objects.get(name="Other").slug, "other-2")
