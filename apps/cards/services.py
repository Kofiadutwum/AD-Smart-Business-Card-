"""Slugs, vCards, QR codes and analytics for public cards."""

import base64
import io
import re

import logging

import segno
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.exceptions import ValidationError
from django.core.files import File
from django.db import transaction
from django.utils import timezone

from apps.core.utils import visitor_hash

from .models import BusinessEvent, BusinessPage, BusinessService, Card, CardEvent, CardPhone, CardSlug, SocialLink
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Slugs (URL-02..06)
# --------------------------------------------------------------------------

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38})[a-z0-9]$")
RESERVED = {
    "admin", "administrator", "login", "logout", "register", "signup", "api", "auth",
    "dashboard", "billing", "staff", "support", "help", "static", "media", "www", "mail",
    "nfc", "card", "cards", "pricing", "about", "contact", "terms", "privacy", "refunds",
    "faq", "settings", "account", "root", "system", "adsmart", "ad-smart", "official",
    "paystack", "payment", "payments", "checkout", "null", "undefined", "test",
}
OFFENSIVE = {"fuck", "shit", "bitch", "cunt", "nigger", "nigga", "porn", "sex", "whore", "slut", "rape"}


def slugify_name(name):
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in (name or ""))
    text = "-".join(part for part in text.split("-") if part)
    return text[:36].strip("-") or "card"


def slug_problem(slug):
    if not SLUG_RE.match(slug or ""):
        return "Use 3 to 40 lowercase letters, numbers and hyphens, starting and ending with a letter or number."
    if "--" in slug:
        return "Hyphens cannot be doubled."
    if slug in RESERVED:
        return "That link is reserved. Try another."
    if any(word in slug.replace("-", "") for word in OFFENSIVE):
        return "Choose a different link."
    return None


def slug_taken(slug, exclude_card=None):
    """Every slug ever used counts as taken (URL-06)."""
    qs = CardSlug.objects.filter(slug=slug)
    if exclude_card is not None:
        qs = qs.exclude(card=exclude_card)  # a card may return to one of its own old links
    return qs.exists() or Card.objects.filter(slug=slug).exclude(pk=getattr(exclude_card, "pk", None)).exists()


def suggest_slug(name):
    base = slugify_name(name)
    if len(base) < 3:
        base = f"{base}-card".strip("-")
    candidate, counter = base, 1
    while slug_problem(candidate) or slug_taken(candidate):
        counter += 1
        candidate = f"{base}-{counter}"
        if counter > 500:
            import secrets

            candidate = f"{base}-{secrets.token_hex(2)}"
            if not slug_taken(candidate):
                break
    return candidate


@transaction.atomic
def create_card(owner, full_name, **fields):
    from apps.billing.services import activate_cards

    card = Card.objects.create(owner=owner, full_name=full_name, slug=suggest_slug(full_name), **fields)
    CardSlug.objects.create(slug=card.slug, card=card, is_current=True)
    subscription = owner.subscription
    if subscription and subscription.is_live:
        activate_cards(owner, subscription)
    return card


# --------------------------------------------------------------------------
# Staff cards and the company card
# --------------------------------------------------------------------------

COMPANY_SLUG = "adgraphics"
COMPANY_SERVICES = [
    ("Logos and brand identity", "A mark and a look your customers remember."),
    ("Flyers, posters and social media designs", "Ready for print and for your feed."),
    ("Printing and packaging", "T-shirts, packaging and printed materials."),
    ("Digital and NFC business cards", "Made, printed and tap-tested in our studio."),
]


def _link_to_website(card):
    """Every staff card links to the website, and its owner cannot hide that link."""
    hidden = [field for field in card.hidden_fields if field != "website"]
    if card.website != settings.SITE_URL or hidden != card.hidden_fields or card.activated_at is None:
        card.website = settings.SITE_URL
        card.hidden_fields = hidden
        card.activated_at = card.activated_at or timezone.now()
        card.save(update_fields=["website", "hidden_fields", "activated_at", "updated_at"])


@transaction.atomic
def ensure_staff_card(user):
    """An administrator's personal card, made automatically. It needs no plan and lives while they are active staff."""
    card = user.cards.filter(is_company=False, deleted_at__isnull=True).first()
    if card is None:
        name = user.full_name or user.email.split("@")[0]
        card = Card.objects.create(
            owner=user, slug=suggest_slug(name), full_name=name, email=user.email,
            website=settings.SITE_URL, activated_at=timezone.now(),
        )
        CardSlug.objects.create(slug=card.slug, card=card, is_current=True)
    else:
        _link_to_website(card)
    return card


def company_card():
    """The business's own card, or None until the main admin first opens the company card page."""
    return Card.objects.select_related("owner").filter(is_company=True, deleted_at__isnull=True).first()


def _logo_as_photo(card):
    path = finders.find("brand/logo-192.png")
    if not path:
        return
    try:
        with open(path, "rb") as handle:
            card.avatar.save("ad-graphics-logo.png", File(handle), save=True)
    except Exception:  # the card works with initials until a photo is uploaded
        logger.warning("Could not store the AD logo on the company card", exc_info=True)


@transaction.atomic
def ensure_company_card():
    """The company card and its business page, created from today's sample the first time they are needed.

    Owned by the oldest active super admin. Returns None when there is no super admin to own it.
    """
    card = company_card()
    if card:
        _link_to_website(card)
        return card
    owner = get_user_model().objects.filter(is_superuser=True, is_active=True).order_by("date_joined", "pk").first()
    if owner is None:
        return None
    from apps.core.models import SiteSettings

    site = SiteSettings.load()
    slug = COMPANY_SLUG if not slug_problem(COMPANY_SLUG) and not slug_taken(COMPANY_SLUG) else suggest_slug("AD Graphics")
    bio = "Brand identity, print and digital design for businesses, churches, schools and events across Ghana."
    card = Card.objects.create(
        owner=owner, slug=slug, is_company=True, full_name="AD Graphics", business_name="AD Graphics",
        job_title="Design and print studio", bio=bio, whatsapp=site.support_whatsapp, email=site.support_email,
        website=settings.SITE_URL, accent="#0048A9", card_colour="#0A0E9C", hide_branding=True,
        activated_at=timezone.now(),
    )
    CardSlug.objects.create(slug=card.slug, card=card, is_current=True)
    CardPhone.objects.create(card=card, number=site.support_phone, label="Office")
    SocialLink.objects.create(card=card, platform="instagram", url=site.instagram_link)
    page, _created = BusinessPage.objects.get_or_create(owner=owner)
    page.is_on, page.tagline, page.about = True, "Design and print studio in Cape Coast", bio
    page.save()
    for position, (title, text) in enumerate(COMPANY_SERVICES):
        BusinessService.objects.create(page=page, title=title, description=text, position=position)
    _logo_as_photo(card)
    return card


@transaction.atomic
def change_slug(card, new_slug):
    """URL-04/05: allowed until an NFC card exists; the old slug redirects."""
    new_slug = new_slug.strip().lower()
    if new_slug == card.slug:
        return
    if card.has_nfc_orders:
        raise ValidationError(_("This card's link is printed on NFC cards, so it can no longer change."))
    problem = slug_problem(new_slug)
    if problem:
        raise ValidationError(problem)
    if slug_taken(new_slug, exclude_card=card):
        raise ValidationError(_("That link is already taken."))
    CardSlug.objects.filter(card=card, is_current=True).update(is_current=False)
    own = CardSlug.objects.filter(slug=new_slug, card=card).first()
    if own:
        own.is_current = True
        own.save(update_fields=["is_current"])
    else:
        CardSlug.objects.create(slug=new_slug, card=card, is_current=True)
    card.slug = new_slug
    card.save(update_fields=["slug", "updated_at"])


def resolve_slug(slug):
    """Return (card, redirect_to_current_slug) or (None, None)."""
    slug = (slug or "").lower()
    card = Card.objects.select_related("owner").filter(slug=slug).first()
    if card:
        return card, None
    history = CardSlug.objects.select_related("card").filter(slug=slug).first()
    if history and history.card:
        return history.card, history.card.slug
    return None, None


def business_page_for(card, viewer=None):
    """The owner's business page when it is switched on and their plan includes one, else None.

    The owner (``viewer``) can also open it while it is still off, to preview it.
    """
    from apps.billing.services import plan_for_card

    page = getattr(card.owner, "business_page", None)  # a missing page raises an AttributeError subclass
    if page is None or not plan_for_card(card).allow_business_page:
        return None
    if card.owner.is_staff and not card.is_company:
        return None  # an administrator's personal card has no business page of its own
    if not page.is_on and not (viewer is not None and viewer.is_authenticated and viewer.pk == card.owner_id):
        return None
    return page


@transaction.atomic
def retire_card(card):
    """Delete a card's public presence; its slugs are never reused."""
    card.deleted_at = timezone.now()
    card.save(update_fields=["deleted_at"])
    CardSlug.objects.filter(card=card).update(retired_at=timezone.now())


# --------------------------------------------------------------------------
# vCard 3.0 (SHR-04, SHR-06)
# --------------------------------------------------------------------------

CRLF = "\r\n"


def _escape(value):
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold(line):
    """RFC 6350 folding at 75 octets; continuation lines start with a space."""
    if len(line.encode("utf-8")) <= 75:
        return line
    chunks, current = [], ""
    for char in line:
        if len((current + char).encode("utf-8")) > 74:
            chunks.append(current)
            current = " " + char
        else:
            current += char
    chunks.append(current)
    return CRLF.join(chunks)


def _split_name(full_name):
    parts = [p for p in (full_name or "").split() if p]
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return "", parts[0], ""
    return parts[-1], parts[0], " ".join(parts[1:-1])


def _photo_line(card):
    """Embed the photo so the contact saves offline, with its picture, on iOS and Android."""
    if not card.avatar:
        return None
    try:
        with card.avatar.open("rb") as handle:
            from PIL import Image

            image = Image.open(handle)
            image = image.convert("RGB")
            image.thumbnail((320, 320))
            out = io.BytesIO()
            image.save(out, format="JPEG", quality=80)
    except Exception:
        return None
    return "PHOTO;ENCODING=b;TYPE=JPEG:" + base64.b64encode(out.getvalue()).decode("ascii")


def build_vcard(card, effective):
    family, given, additional = _split_name(card.full_name)
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"N:{_escape(family)};{_escape(given)};{_escape(additional)};;",
        f"FN:{_escape(card.full_name)}",
    ]
    if card.business_name:
        lines.append(f"ORG:{_escape(card.business_name)}")
    if card.job_title:
        lines.append(f"TITLE:{_escape(card.job_title)}")
    numbers = set()
    if card.shows("phones"):
        for phone in effective["phones"]:
            label = re.sub(r"[^A-Za-z0-9-]", "", phone.label or "") or "CELL"
            lines.append(f"TEL;TYPE={_escape(label.upper())},VOICE:{_escape(phone.number)}")
            numbers.add(phone.number)
    if card.whatsapp and card.shows("whatsapp") and card.whatsapp not in numbers:
        lines.append(f"TEL;TYPE=CELL:{_escape(card.whatsapp)}")
    if card.email and card.shows("email"):
        lines.append(f"EMAIL;TYPE=INTERNET,WORK:{_escape(card.email)}")
    if card.website and card.shows("website"):
        lines.append(f"URL:{_escape(card.website)}")
    if card.address and card.shows("address"):
        lines.append(f"ADR;TYPE=WORK:;;{_escape(card.address)};;;;")
    if card.bio and card.shows("bio"):
        lines.append(f"NOTE:{_escape(card.bio)}")
    for link in effective["social_links"]:
        lines.append(f"X-SOCIALPROFILE;TYPE={_escape(link.platform)}:{_escape(link.url)}")
    lines.append(f"URL;TYPE=card:{_escape(card.public_url())}")
    photo = _photo_line(card)
    if photo:
        lines.append(photo)
    lines.append("END:VCARD")
    return CRLF.join(_fold(line) for line in lines) + CRLF


def vcard_filename(card):
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in card.full_name or "contact")
    return f"{safe.strip('-').lower() or 'contact'}.vcf"


# --------------------------------------------------------------------------
# QR codes (SHR-03). Error correction 'h' survives scratches on print.
# --------------------------------------------------------------------------


def qr_svg(data, scale=6, dark="#14161a", light=None, css_class="qr"):
    out = io.BytesIO()
    segno.make(data, error="h").save(
        out, kind="svg", scale=scale, dark=dark, light=light, border=2,
        xmldecl=False, svgns=True, svgclass=css_class, lineclass="qr-line",
    )
    return out.getvalue().decode("utf-8")


def qr_png(data, scale=12):
    out = io.BytesIO()
    segno.make(data, error="h").save(out, kind="png", scale=scale, dark="#14161a", light="#ffffff", border=2)
    return out.getvalue()


# --------------------------------------------------------------------------
# Analytics (Section 11)
# --------------------------------------------------------------------------

BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|facebookexternalhit|facebookcatalog|whatsapp|telegram|twitterbot|"
    r"linkedinbot|slackbot|discordbot|skypeuripreview|preview|embedly|pinterest|vkshare|"
    r"headless|python-requests|curl|wget|httpclient|okhttp|go-http|lighthouse",
    re.IGNORECASE,
)


def source_from(request):
    """?src= per the SRS, and ?s= as written by the Flask site onto early NFC tags."""
    value = (request.GET.get("src") or request.GET.get("s") or "link").lower()
    return value if value in {"link", "qr", "nfc"} else "link"


def should_count(request, card):
    agent = request.META.get("HTTP_USER_AGENT", "")
    if not agent or BOT_RE.search(agent):
        return False  # ANL-02
    if request.user.is_authenticated and request.user.pk == card.owner_id:
        return False  # ANL-03
    return True


def record(request, card, kind, detail="", source=None):
    if not should_count(request, card):
        return
    try:
        CardEvent.objects.create(
            card=card,
            kind=kind,
            source=source or source_from(request),
            detail=detail[:32],
            visitor_hash=visitor_hash(request),
        )
    except Exception:  # analytics must never break a card
        pass


def record_business(request, card, page, kind, product=None):
    """A shop page or product opened, a WhatsApp buy tapped, an enquiry sent. One a day per visitor, kind and product."""
    if not should_count(request, card):
        return
    visitor = visitor_hash(request)
    today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        seen = BusinessEvent.objects.filter(
            page=page, product=product, kind=kind, visitor_hash=visitor, occurred_at__gte=today
        ).exists()
        if not seen:
            BusinessEvent.objects.create(page=page, product=product, kind=kind, visitor_hash=visitor)
    except Exception:  # analytics must never break a shop page
        pass
