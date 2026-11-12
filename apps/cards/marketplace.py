"""The shop marketplace at /shops/ and the landing page's shop window.

One cached snapshot (ten minutes) holds every listed shop with its products, ranked by what visitors did in
the last 30 days. The marketplace, its search and the landing slider all read that snapshot, so a busy page
costs one set of queries every ten minutes, not one per visitor.
"""

import math
import random
import re
import unicodedata
from datetime import timedelta
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.utils import whatsapp_digits

from .colors import valid_hex
from .models import BusinessEvent, BusinessPage, BusinessProduct, ProductCategory

CACHE_KEY = "marketplace:v1"
CACHE_SECONDS = 600
WINDOW_DAYS = 30
# A product opened counts 1, a "Buy" tap 3, an enquiry 5.
WEIGHTS = {BusinessEvent.PRODUCT: 1, BusinessEvent.BUY: 3, BusinessEvent.ENQUIRY: 5}
SLIDER_SIZE = 10
SLIDER_MIN = 3  # fewer than this and the landing page shows no slider
SLIDE_PX = 220  # keep in step with --slide-w in marketplace.css
RUN_PX = 1500  # one run of slides must be wider than any screen
ROW_MAX = 10  # products in one row on /shops
SHOP_PREVIEW = 2 * ROW_MAX  # products shown per shop on /shops; the rest are one tap away on the shop's own page
OTHER = "other"
FUZZY = 0.75


# --------------------------------------------------------------------------
# WhatsApp
# --------------------------------------------------------------------------


def whatsapp_target(card):
    """How buyers reach the shop: the card's WhatsApp number, else a WhatsApp link the owner pasted, else nothing."""
    digits = whatsapp_digits(card.whatsapp)
    if digits:
        return {"digits": digits}
    link = card.social_links.filter(platform="whatsapp").values_list("url", flat=True).first()
    return {"url": link} if link else None


def buy_url(target, text):
    """The chat link with the message already typed. The buyer still presses Send: WhatsApp allows nothing else."""
    if not target:
        return ""
    if target.get("digits"):
        return f"https://wa.me/{target['digits']}?text={quote(text, safe='')}"
    parts = urlsplit(target["url"])
    host = (parts.hostname or "").removeprefix("www.")
    if host not in {"wa.me", "api.whatsapp.com", "web.whatsapp.com"}:
        return target["url"]  # a group invite or similar: open it as the owner wrote it, with no typed message
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "text"] + [("text", text)]
    return urlunsplit(parts._replace(query=urlencode(query, quote_via=quote)))


def buy_text(shop_name, title, price, link):
    values = {"shop": shop_name, "product": title, "price": price, "link": link}
    if price:
        return _("Hi %(shop)s, I would like to buy %(product)s (%(price)s). %(link)s") % values
    return _("Hi %(shop)s, I would like to buy %(product)s. %(link)s") % values


def cart_intro(shop_name):
    """The first line of a cart message. The page adds one line per product and the shop's link after it."""
    return _("Hi %(shop)s, I would like to order:") % {"shop": shop_name}


def split_rows(products):
    """One row for up to 10 products; more than that, two rows of equal length (the first may hold one more)."""
    if len(products) <= ROW_MAX:
        return [products] if products else []
    half = (len(products) + 1) // 2
    return [products[:half], products[half:]]


def with_buy_links(shop):
    """A copy of the shop whose shown products carry their WhatsApp link, in the visitor's language, and sit in rows."""
    shown = [
        {**p, "buy": buy_url(shop["wa"], buy_text(shop["name"], p["title"], p["price"], p["link"]))}
        for p in shop.get("shown", shop["products"])
    ]
    return {**shop, "shown": shown, "rows": split_rows(shown)}


# --------------------------------------------------------------------------
# The snapshot
# --------------------------------------------------------------------------


def _words(*texts, limit=60):
    text = unicodedata.normalize("NFKD", " ".join(t for t in texts if t).lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    seen = []
    for word in re.findall(r"\w+", text):
        if word not in seen:
            seen.append(word)
    return seen[:limit]


def _build():
    from apps.billing.services import plan_for_card  # billing imports cards; load it late

    from .services import business_page_for

    since = timezone.now() - timedelta(days=WINDOW_DAYS)
    categories = {c.pk: c for c in ProductCategory.objects.filter(is_active=True)}
    pages = (
        BusinessPage.objects.filter(is_on=True, in_marketplace=True, marketplace_hidden=False)
        .select_related("owner").prefetch_related("products")
    )
    shops = []
    for page in pages:
        card = page.primary_card()
        if card is None or not card.is_live or business_page_for(card) is None:
            continue
        limit = plan_for_card(card).max_products
        visible = [p for p in page.products.all() if not p.is_hidden][:limit]
        if not visible:
            continue
        eff = card.effective()
        logo = eff["logo"] or card.avatar
        name = card.business_name or card.full_name
        page_path = reverse("cards:business", args=[card.slug])
        shop = {
            "id": page.pk, "slug": card.slug, "name": name, "tagline": page.tagline, "url": page_path,
            "logo": logo.url if logo else "", "accent": eff["accent"] if valid_hex(eff["accent"]) else "#8805a9",
            "wa": whatsapp_target(card), "score": 0,
            "words": _words(name, page.tagline), "products": [],
        }
        for product in visible:
            category = categories.get(product.category_id)
            shop["products"].append({
                "id": product.pk, "title": product.title, "price": product.price,
                "text": product.description[:140], "image": product.image.url,
                "category": category.slug if category else OTHER, "views": 0,
                "shop": name, "shop_slug": card.slug, "shop_url": page_path,
                "url": f"{page_path}?p={product.pk}", "link": f"{settings.SITE_URL}{page_path}?p={product.pk}",
                "words": _words(product.title, product.description, category.name if category else "", name),
            })
        shops.append(shop)

    window = BusinessEvent.objects.filter(page_id__in=[s["id"] for s in shops], occurred_at__gte=since)
    by_shop = {s["id"]: s for s in shops}
    for page_id, kind, count in window.filter(kind__in=WEIGHTS).values_list("page_id", "kind").annotate(n=Count("id")):
        by_shop[page_id]["score"] += WEIGHTS[kind] * count
    viewed = dict(window.filter(kind=BusinessEvent.PRODUCT).exclude(product=None).values_list("product_id").annotate(n=Count("id")))
    for shop in shops:
        for product in shop["products"]:
            product["views"] = viewed.get(product["id"], 0)
    shops.sort(key=lambda s: (-s["score"], s["name"].lower()))

    top = sorted((p for s in shops for p in s["products"] if p["views"] > 0), key=lambda p: -p["views"])[:SLIDER_SIZE]
    counts = {}
    for shop in shops:
        for product in shop["products"]:
            counts[product["category"]] = counts.get(product["category"], 0) + 1
    sidebar = [{"slug": c.slug, "name": c.name, "count": counts[c.slug]} for c in categories.values() if counts.get(c.slug)]
    if counts.get(OTHER):
        sidebar.append({"slug": OTHER, "name": "", "count": counts[OTHER]})  # named in the template, so it is translated
    return {"shops": shops, "top": top, "categories": sidebar}


def get_data():
    data = cache.get(CACHE_KEY)
    if data is None:
        data = _build()
        cache.set(CACHE_KEY, data, CACHE_SECONDS)
    return data


def clear():
    cache.delete(CACHE_KEY)


@receiver([post_save, post_delete], sender=BusinessPage)
@receiver([post_save, post_delete], sender=BusinessProduct)
@receiver([post_save, post_delete], sender=ProductCategory)
def _changed(sender, **kwargs):
    clear()


def slider():
    """The landing page's shop window: the most opened products of the last 30 days, in a fresh order each time.

    The run of slides is repeated until it is wider than any screen, or the endless loop shows a gap at the end.
    Only the first run is flagged for screen readers; the repeats carry ``dup``.
    """
    top = get_data()["top"]
    if len(top) < SLIDER_MIN:
        return []
    items = list(top)
    random.shuffle(items)
    copies = math.ceil(RUN_PX / (SLIDE_PX * len(items)))
    return [{**p, "dup": n > 0} for n in range(copies) for p in items]


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------


def _hits(word, words):
    """Does ``word`` appear in ``words``, allowing a slip of the finger (so "shose" finds "shoes")?"""
    if any(word in w for w in words):
        return True
    if len(word) < 4:
        return False
    matcher = SequenceMatcher(None)
    matcher.set_seq2(word)
    for w in words:
        matcher.set_seq1(w)
        if matcher.real_quick_ratio() >= FUZZY and matcher.quick_ratio() >= FUZZY and matcher.ratio() >= FUZZY:
            return True
    return False


def _all_hit(query_words, words):
    return all(_hits(word, words) for word in query_words)


def search(shops, query="", category=""):
    """Shops (best first) narrowed by what the visitor typed and the category they picked.

    shortcut: difflib over the in-memory snapshot, fine to a few thousand products; move to a real search index beyond that.
    """
    words = _words(query, limit=8)
    found = []
    for shop in shops:
        products = shop["products"]
        if category:
            products = [p for p in products if p["category"] == category]
        if words and not _all_hit(words, shop["words"]):  # the shop's own name matching keeps all its products
            products = [p for p in products if _all_hit(words, p["words"])]
        if products:
            found.append({**shop, "products": products, "shown": products[:SHOP_PREVIEW]})
    return found


# --------------------------------------------------------------------------
# What an owner sees about their own shop
# --------------------------------------------------------------------------


def shop_stats(page):
    """Product views, buy taps and enquiries of the last 30 days, for the owner's dashboard."""
    since = timezone.now() - timedelta(days=WINDOW_DAYS)
    rows = BusinessEvent.objects.filter(page=page, occurred_at__gte=since).values_list("product_id", "kind").annotate(n=Count("id"))
    products, totals = {}, {BusinessEvent.VIEW: 0, BusinessEvent.PRODUCT: 0, BusinessEvent.BUY: 0, BusinessEvent.ENQUIRY: 0}
    for product_id, kind, count in rows:
        totals[kind] += count
        if product_id:
            products.setdefault(product_id, {BusinessEvent.PRODUCT: 0, BusinessEvent.BUY: 0})[kind] = count
    return {"products": products, "totals": totals}
