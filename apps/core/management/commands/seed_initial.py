"""Load the starting business data from SRS v2.2. Safe to run more than once.

    python manage.py seed_initial            # plans, NFC tiers, delivery zones, settings
    python manage.py seed_initial --gallery  # also load the studio's gallery slides
"""

from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from apps.billing.models import ExchangeRate, Plan
from apps.core.models import SiteSettings
from apps.core.utils import process_image
from apps.marketing.models import GalleryImage
from apps.nfc.models import DeliveryZone, PriceTier

PLANS = [
    # Section 5.1 prices and the 5.2 feature matrix (Proposed).
    dict(code="basic", name="Basic", price_minor=10000, sort_order=1, max_cards=1, max_phones=2,
         max_social_links=3, all_templates=False, custom_colours=False, allow_logo=False,
         allow_advanced_style=False, branding_footer="shown", full_analytics=False, priority_support=False,
         blurb="Everything you need to share one card."),
    dict(code="professional", name="Professional", price_minor=15000, sort_order=2, max_cards=1, max_phones=3,
         max_social_links=None, all_templates=True, custom_colours=True, allow_logo=True,
         allow_advanced_style=True, branding_footer="small", full_analytics=True, priority_support=False,
         is_featured=True, blurb="Your brand, your colours, and full analytics."),
    dict(code="business", name="Business", price_minor=50000, sort_order=3, max_cards=5, max_phones=3,
         max_social_links=None, all_templates=True, custom_colours=True, shared_brand_theme=True, allow_logo=True,
         allow_advanced_style=True, branding_footer="removable", full_analytics=True, priority_support=True,
         blurb="Up to five team cards under one brand."),
]

TIERS = [
    # Section 7.1 (corrected): no larger order ever costs less than a smaller one.
    dict(min_quantity=1, max_quantity=4, unit_price_minor=20000, mode=PriceTier.FLAT),
    dict(min_quantity=5, max_quantity=9, unit_price_minor=16000, mode=PriceTier.FLAT),
    dict(min_quantity=10, max_quantity=20, unit_price_minor=15000, mode=PriceTier.FLAT),
    dict(min_quantity=21, max_quantity=None, unit_price_minor=13500, mode=PriceTier.INCREMENTAL),
]

ZONES = [
    # DLV-01 (Proposed). Fees are placeholders until the business sets them.
    dict(name="Pickup from the AD Graphics office", description="Collect in person. Free.", fee_minor=0,
         eta="Ready the day your cards pass quality checks", is_pickup=True, sort_order=1),
    dict(name="Delivery within Cape Coast", description="Rider delivery to your door.", fee_minor=2000,
         eta="1 working day after dispatch", sort_order=2),
    dict(name="Delivery to other regions of Ghana", description="By courier to your nearest office or door.",
         fee_minor=4000, eta="2–4 working days after dispatch", sort_order=3),
]

INTERNATIONAL_ZONES = [
    # Shipping outside Ghana (added after SRS DLV-02 by the business). The
    # customer pays shipping and delivery; import duties are the recipient's.
    # Fees are placeholders until the business sets them in the staff area.
    dict(name="West Africa", description="Nigeria, Côte d’Ivoire, Togo, Benin, Burkina Faso and neighbours.",
         fee_minor=35000, eta="3–7 working days after dispatch", sort_order=10),
    dict(name="Rest of Africa", description="International courier to the rest of Africa.",
         fee_minor=55000, eta="5–10 working days after dispatch", sort_order=11),
    dict(name="UK and Europe", description="International courier to the UK and Europe.",
         fee_minor=65000, eta="5–10 working days after dispatch", sort_order=12),
    dict(name="USA, Canada and rest of the world", description="International courier anywhere else.",
         fee_minor=80000, eta="5–12 working days after dispatch", sort_order=13),
]

GALLERY = [
    ("105ef85f15514e91a4da45a2391ffd5d.jpg", "2.5k+ designs and counting.",
     "A thank-you to the clients who made AD Graphics what it is today.", "Milestone", "top",
     "Gold 2.5k+ designs announcement from AD Graphics on a purple background"),
    ("15e63b9adcb846e0b337752fc09cf17d.jpg", "Free business branding for UCC finalists.",
     "Logo, flyers, T-shirt and packaging designs, offered to the GHAMSU UCC Local 2024/25 finalists.", "Campaign", "top",
     "Purple flyer announcing free business branding with a QR code to register"),
    ("80d0882f18d64f10b99834840be771a6.jpg", "We always see what you see.",
     "Tell us your plan in detail and we will design exactly what you really want.", "Brand campaign", "center",
     "Blue AD Graphics advert with a woman wearing a VR headset"),
    ("e7c67f18926346488c01cb92ad940ce1.jpg", "Ohw, it’s Friday already.",
     "Playful weekend social content for the AD Graphics feed.", "Social media", "center",
     "Orange Friday social post with a surprised girl in glasses"),
]


class Command(BaseCommand):
    help = "Load plans, NFC price tiers, delivery zones and settings from SRS v2.2."

    def add_arguments(self, parser):
        parser.add_argument("--gallery", action="store_true", help="Also load the starter gallery slides.")
        parser.add_argument("--offline", action="store_true", help="Do not fetch today's exchange rate.")

    def handle(self, *args, **options):
        if options.get("verbosity", 1) == 0:
            self.stdout.write = lambda *a, **k: None
        SiteSettings.load()
        for data in PLANS:
            code = data["code"]
            fields = {k: v for k, v in data.items() if k != "code"}
            _, created = Plan.objects.get_or_create(code=code, defaults=fields)
            self.stdout.write(f"{'Created' if created else 'Kept'} plan {code}")
        if not PriceTier.objects.exists():
            for data in TIERS:
                PriceTier.objects.create(**data)
            self.stdout.write("Created NFC price tiers")
        if not DeliveryZone.objects.exists():
            for data in ZONES:
                DeliveryZone.objects.create(**data)
            self.stdout.write("Created delivery zones (set real fees in Staff › Settings › Pricing)")
        if not DeliveryZone.objects.filter(is_international=True).exists():
            for data in INTERNATIONAL_ZONES:
                DeliveryZone.objects.create(is_international=True, **data)
            self.stdout.write("Created international shipping zones (placeholder fees; set real ones in the staff area)")
        if not ExchangeRate.objects.exists() and not options.get("offline"):
            from apps.billing.fx import refresh_rate

            self.stdout.write(f"Exchange rate: {refresh_rate() or 'provider unavailable (USD prices stay hidden)'}")
        if options["gallery"] and not GalleryImage.objects.exists():
            folder = Path(__file__).resolve().parents[3] / "marketing" / "seed_gallery"
            for order, (filename, title, description, caption, focus, alt) in enumerate(GALLERY):
                path = folder / filename
                if not path.exists():
                    continue
                with path.open("rb") as handle:
                    upload = ContentFile(handle.read(), name=filename)
                    upload.size = path.stat().st_size
                    processed = process_image(upload, folder="gallery", max_px=1600, max_bytes=20 * 1024 * 1024)
                image = GalleryImage(
                    title=title, description=description, caption=caption, focus=focus, alt_text=alt,
                    display_order=order, action_label="View design",
                )
                image.image.save(processed.name, processed, save=False)
                image.save()
            self.stdout.write("Loaded gallery slides")
        self.stdout.write(self.style.SUCCESS("Seed complete."))
