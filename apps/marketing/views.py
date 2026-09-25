"""Public marketing pages (Section 26)."""

from django.http import HttpResponse
from django.shortcuts import render

from apps.billing.fx import latest_rate
from apps.billing.services import active_plans
from apps.core.models import SiteSettings
from apps.nfc import services as nfc
from apps.nfc.models import DeliveryZone

from .content import HOW_IT_WORKS, SHARING, faq
from .demo import demo_card, demo_vcard
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
        "rate": latest_rate(),
        "tiers": tier_list,
        "tiers_json": nfc.tiers_json(tier_list),
        "nfc_examples": examples,
        "zones": DeliveryZone.objects.filter(is_active=True),
    }


def home(request):
    site = SiteSettings.load()
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
            "faq": faq(site)[:6],
            "demo": demo_card(site),
            "over_hero": True,
        }
    )
    return render(request, "marketing/home.html", context)


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


def demo_vcf(request):
    """'Save contact' on the homepage sample card: AD Graphics' real details."""
    return HttpResponse(
        demo_vcard(SiteSettings.load()),
        content_type="text/vcard; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="kofi-adutwum-ad-graphics.vcf"'},
    )
