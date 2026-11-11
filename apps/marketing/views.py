"""Public marketing pages (Section 26)."""

import hashlib
import json
from urllib.parse import urlencode

from django.conf import settings
from django.core.paginator import Paginator
from django.contrib.staticfiles import finders
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import get_language, gettext
from django.views.decorators.clickjacking import xframe_options_sameorigin

from apps.billing.fx import latest_rate
from apps.billing.services import TEAM_MAX_SEATS, active_plans
from apps.cards import marketplace
from apps.cards.services import business_page_for, company_card
from apps.cards.views import business_context
from apps.core.models import SiteSettings
from apps.nfc import services as nfc
from apps.nfc.models import DeliveryZone

from .content import HOW_IT_WORKS, SHARING, faq
from .demo import company_demo, demo_business, demo_card, demo_vcard
from .models import GalleryImage

EXAMPLE_QUANTITIES = [1, 4, 5, 10, 20, 21, 30, 50]


def _pricing_context():
    tier_list = nfc.tiers()
    examples = []
    for quantity in EXAMPLE_QUANTITIES:
        try:
            examples.append((quantity, nfc.cards_price(quantity, tier_list)))
        except ValueError:
            pass
    return {
        "plans": list(active_plans()),
        "team_max": TEAM_MAX_SEATS,
        "rate": latest_rate(),
        "tiers": tier_list,
        "tiers_json": nfc.tiers_json(tier_list),
        "nfc_examples": examples,
        "zones": DeliveryZone.objects.filter(is_active=True),
    }


def _company():
    """The company card when it exists and is live: the landing page's phones then show it, not the fixed sample."""
    card = company_card()
    return card if card and card.is_live else None


def home(request):
    site = SiteSettings.load()
    company = _company()
    context = _pricing_context()
    gallery = list(GalleryImage.objects.filter(is_published=True))
    context.update(
        {
            "gallery": gallery,
            "gallery_json": [
                {
                    "id": image.pk,
                    "title": image.title,
                    "description": image.description,
                    "image": image.image.url,
                    "imageAlt": image.alt_text or image.title,
                    "overlay": image.caption,
                    "focus": image.focus,
                }
                for image in gallery
            ],
            "how_it_works": HOW_IT_WORKS,
            "sharing": SHARING,
            "faq": faq(site)[:4],
            "demo": company_demo(company) if company else demo_card(site),
            "market_slides": marketplace.slider(),
            "over_hero": True,
        }
    )
    return render(request, "marketing/home.html", context)


SHOPS_PER_PAGE = 8


def shops(request):
    """The marketplace: every listed shop with its products, the busiest shops first, with search and categories."""
    data = marketplace.get_data()
    query = request.GET.get("q", "").strip()[:80]
    category = request.GET.get("c", "")[:70]
    found = marketplace.search(data["shops"], query, category)
    page = Paginator(found, SHOPS_PER_PAGE).get_page(request.GET.get("page"))
    context = {
        "page": page,
        "shops": [marketplace.with_buy_links(shop) for shop in page],
        "query": query,
        "category": category,
        "categories": data["categories"],
        "all_count": sum(len(shop["products"]) for shop in data["shops"]),
        "keep": urlencode({k: v for k, v in (("q", query), ("c", category)) if v}),
    }
    return render(request, "marketing/shops.html", context)


@xframe_options_sameorigin  # shown in a phone frame on the home page; the site otherwise refuses to be framed
def demo_business_page(request):
    company = _company()
    page = business_page_for(company) if company else None
    if page:
        return render(request, "cards/business.html", business_context(page, demo=True, preview=False))
    gallery = GalleryImage.objects.filter(is_published=True)
    return render(request, "cards/business.html", demo_business(SiteSettings.load(), list(gallery)) | {"demo": True})


def pricing(request):
    return render(request, "marketing/pricing.html", _pricing_context())


def nfc_cards(request):
    context = _pricing_context()
    context["faq"] = [item for item in faq(SiteSettings.load()) if "NFC" in item["q"] or "deliver" in item["q"].lower() or "cancel" in item["q"].lower()]
    return render(request, "marketing/nfc.html", context)


def faq_page(request):
    return render(request, "marketing/faq.html", {"faq": faq(SiteSettings.load())})


def legal(request, page):
    return render(request, f"marketing/legal_{page}.html", {"page": page})


SITEMAP_PAGES = ["marketing:home", "marketing:shops", "marketing:pricing", "marketing:nfc", "marketing:faq", "marketing:terms",
                 "marketing:privacy", "marketing:refunds"]
# Everything behind a sign-in, a payment or a staff login, which search engines have no use for.
ROBOTS_DISALLOW = ["/dashboard/", "/staff/", "/auth/", "/billing/", "/nfc/", "/internal/", "/prefs/"]


def robots_txt(request):
    lines = ["User-agent: *", *(f"Disallow: {path}" for path in ROBOTS_DISALLOW), "", f"Sitemap: {settings.SITE_URL}/sitemap.xml", ""]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


# --- the installable app (PWA) -------------------------------------------------
# The browser installs the site as an app from these three: the manifest says what
# the app is called and looks like, the service worker lets it open without a
# connection, and the offline page is what it shows then.


def manifest(request):
    icons = [
        {"src": static("brand/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": static("brand/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": static("brand/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ]
    data = {
        "id": "/",
        "name": settings.SITE_NAME,
        "short_name": "AD Smart",
        "description": gettext("Share your contact details by link, QR code or NFC tap."),
        "lang": get_language(),
        "start_url": "/dashboard/?source=app",  # owners land on their dashboard; anyone else is asked to log in
        "scope": "/",
        "display": "standalone",
        "background_color": "#1f1f1f",
        "theme_color": "#1f1f1f",
        "categories": ["business", "productivity"],
        "icons": icons,
    }
    return JsonResponse(data, content_type="application/manifest+json")


def offline(request):
    return render(request, "pwa/offline.html")


def touch_icon(request):
    """An iPhone asks for its home-screen icon at the site root as well as at the link in the page; answer both with the logo."""
    path = finders.find("brand/apple-touch-icon.png")
    if not path:
        raise Http404
    return FileResponse(open(path, "rb"), content_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


def service_worker(request):
    """Served from the site root so it can cover every page. The cache name changes whenever the
    offline page or a static file it keeps changes, so a deploy replaces the old cache by itself."""
    page = render_to_string("pwa/offline.html", request=request)
    keep = [static("brand/logo-96.webp")]
    version = hashlib.sha1((page + "".join(keep)).encode()).hexdigest()[:10]
    response = render(
        request,
        "pwa/sw.js",
        {"cache_name": f"adsmart-{version}", "keep_json": json.dumps(["/offline", *keep]), "debug": settings.DEBUG},
        content_type="application/javascript; charset=utf-8",
    )
    response["Cache-Control"] = "no-cache"  # the browser re-checks it on every visit, so updates arrive
    response["Service-Worker-Allowed"] = "/"
    return response


def sitemap_xml(request):
    urls = "".join(f"<url><loc>{settings.SITE_URL}{reverse(name)}</loc></url>" for name in SITEMAP_PAGES)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return HttpResponse(xml, content_type="application/xml; charset=utf-8")


def demo_vcf(request):
    """'Save contact' on the homepage sample card: AD Graphics' real details."""
    return HttpResponse(
        demo_vcard(SiteSettings.load()),
        content_type="text/vcard; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="kofi-adutwum-ad-graphics.vcf"'},
    )
