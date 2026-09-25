"""The sample card shown on the homepage, rendered by the real card template.

It is a working card: Call, WhatsApp and Email reach AD Graphics, and "Save
contact" downloads AD Graphics' details as a real .vcf.
"""

import base64
import io
from types import SimpleNamespace

from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.urls import reverse
from PIL import Image

from apps.cards.colors import palette
from apps.cards.services import CRLF, _escape, _fold


def demo_card(site):
    card = SimpleNamespace(
        slug="kofi-adutwum",
        full_name="Kofi Adutwum",
        job_title="CEO",
        business_name="AD Graphics",
        bio="Brand identity, print and digital design for businesses, churches, schools and events across Ghana.",
        # The AD logo sits in the profile circle.
        avatar=SimpleNamespace(url=static("brand/logo-192.webp")),
        avatar_is_logo=True,
        avatar_alt="AD Graphics logo",
        initials="KA",
        whatsapp=site.support_whatsapp,
        email=site.support_email,
        website="",
        address="",
        ghana_post_gps="",
        latitude=None,
        longitude=None,
        hidden_fields=[],
    )
    effective = {
        "template": "classic",
        "button_style": "pill",
        "icon_layout": "list",
        "logo": None,
        "background": None,
        "phones": [SimpleNamespace(number=site.support_phone, label="Office")],
        "social_links": [
            SimpleNamespace(
                url=site.instagram_link, platform="instagram", label="Instagram", brand_icon="instagram"
            ),
        ],
        "branding": "shown",
    }
    return {
        "card": card,
        "eff": effective,
        "pal": palette("#0048A9", "#0A0E9C"),
        "card_url": settings.SITE_URL + "/",
        "vcf_url": reverse("marketing:demo_vcf"),
        "demo": True,
    }


def _logo_photo_line():
    """The AD logo on white, as the contact photo."""
    path = finders.find("brand/logo-192.png")
    if not path:
        return None
    logo = Image.open(path).convert("RGBA")
    canvas = Image.new("RGB", (240, 240), "white")
    logo.thumbnail((190, 190))
    canvas.paste(logo, ((240 - logo.width) // 2, (240 - logo.height) // 2), logo)
    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=85)
    return "PHOTO;ENCODING=b;TYPE=JPEG:" + base64.b64encode(out.getvalue()).decode("ascii")


def demo_vcard(site):
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        "N:Adutwum;Kofi;;;",
        "FN:Kofi Adutwum",
        f"ORG:{_escape('AD Graphics')}",
        "TITLE:CEO",
        f"TEL;TYPE=WORK,VOICE:{_escape(site.support_phone)}",
        f"TEL;TYPE=CELL:{_escape(site.support_whatsapp)}",
        f"EMAIL;TYPE=INTERNET,WORK:{_escape(site.support_email)}",
        f"URL:{_escape(settings.SITE_URL)}",
        f"X-SOCIALPROFILE;TYPE=instagram:{_escape(site.instagram_link)}",
        "NOTE:AD Smart Business Cards · digital and NFC business cards by AD Graphics.",
    ]
    photo = _logo_photo_line()
    if photo:
        lines.append(photo)
    lines.append("END:VCARD")
    return CRLF.join(_fold(line) for line in lines) + CRLF
