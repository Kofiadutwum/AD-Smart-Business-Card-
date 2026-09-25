"""Small helpers shared by every app."""

import hashlib
import io
import re
import uuid
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

# --------------------------------------------------------------------------
# Money. Amounts are integers of pesewas everywhere; floats lose pennies.
# --------------------------------------------------------------------------


def format_ghs(minor):
    """12345 -> 'GHS 123.45' (NFR-09)."""
    amount = Decimal(int(minor or 0)) / 100
    return f"GHS {amount:,.2f}"


def ghs_to_minor(value):
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def percent_of(minor, percent):
    return int(
        (Decimal(int(minor)) * Decimal(str(percent)) / Decimal(100)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


# --------------------------------------------------------------------------
# Phone numbers
# --------------------------------------------------------------------------

GH_LOCAL = re.compile(r"^0\d{9}$")


def normalise_phone(raw):
    """Return an E.164 number or raise ValidationError.

    Ghanaian formats (024 412 3456, 233244123456, +233 24 412 3456) become
    +233244123456. Other countries must be written with their + prefix.
    """
    if not raw:
        raise ValidationError("Enter a phone number.")
    text = str(raw).strip()
    plus = text.startswith("+") or text.startswith("00")
    digits = re.sub(r"\D", "", text)
    if text.startswith("00"):
        digits = digits[2:]
    if not plus and GH_LOCAL.match(digits):
        return "+233" + digits[1:]
    if digits.startswith("233"):
        if len(digits) != 12:
            raise ValidationError("A Ghanaian number has 10 digits, for example 024 412 3456.")
        return "+" + digits
    if plus and 8 <= len(digits) <= 15:
        return "+" + digits
    raise ValidationError(
        "Enter a Ghanaian number such as 024 412 3456, or an international number starting with +."
    )


def display_phone(e164):
    """+233244123456 -> '+233 24 412 3456'. Other numbers are left alone."""
    if e164 and e164.startswith("+233") and len(e164) == 13:
        rest = e164[4:]
        return f"+233 {rest[:2]} {rest[2:5]} {rest[5:]}"
    return e164 or ""


def whatsapp_digits(e164):
    return re.sub(r"\D", "", e164 or "")


def keep_name(instance, filename):
    """upload_to that keeps the folder/name the app already chose."""
    return filename


# --------------------------------------------------------------------------
# Uploads (SEC-07, NFR-02)
# --------------------------------------------------------------------------

RASTER_TYPES = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}


def process_image(upload, *, folder, max_px=800, max_bytes=None):
    """Validate an uploaded picture and return a resized WebP ContentFile.

    Pillow must be able to decode it as JPEG, PNG or WebP; the file name and
    the browser's content type are not trusted.
    """
    max_bytes = max_bytes or settings.MAX_IMAGE_UPLOAD_BYTES
    if upload.size > max_bytes:
        raise ValidationError(f"That image is larger than {max_bytes // (1024 * 1024)} MB.")
    try:
        image = Image.open(upload)
        image_format = image.format
        image.load()
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValidationError("Upload a JPG, PNG or WebP image.")
    if image_format not in RASTER_TYPES:
        raise ValidationError("Upload a JPG, PNG or WebP image.")

    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    image.thumbnail((max_px, max_px), Image.LANCZOS)

    out = io.BytesIO()
    image.save(out, format="WEBP", quality=82, method=6)
    return ContentFile(out.getvalue(), name=f"{folder}/{uuid.uuid4().hex}.webp")


SVG_ALLOWED_TAGS = {
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "defs", "lineargradient", "radialgradient", "stop", "clippath", "mask", "title",
    "desc", "text", "tspan", "use", "symbol",
}


def sanitise_svg(data):
    """Strip scripts, event handlers and external references from an SVG."""
    from defusedxml import ElementTree as SafeET

    try:
        root = SafeET.fromstring(data)
    except Exception:
        raise ValidationError("That SVG file could not be read.")

    def local(tag):
        return tag.rsplit("}", 1)[-1].lower()

    if local(root.tag) != "svg":
        raise ValidationError("That file is not an SVG image.")

    def clean(element):
        for child in list(element):
            if local(child.tag) not in SVG_ALLOWED_TAGS:
                element.remove(child)
                continue
            clean(child)
        for attr in list(element.attrib):
            name = local(attr)
            value = element.attrib[attr].strip().lower()
            if name.startswith("on") or name == "style" and "url(" in value:
                del element.attrib[attr]
            elif name == "href" and not value.startswith("#"):
                del element.attrib[attr]

    clean(root)
    from xml.etree import ElementTree as ET

    return ET.tostring(root, encoding="utf-8")


def validate_design_file(upload, *, folder):
    """Logos and reference designs for printing: JPG, PNG, WebP, SVG or PDF up to 10 MB.

    Raster files are kept at full resolution because they are going to print.
    """
    if upload.size > settings.MAX_DESIGN_UPLOAD_BYTES:
        raise ValidationError("Design files can be up to 10 MB.")
    head = upload.read(2048)
    upload.seek(0)
    if head.startswith(b"%PDF"):
        return ContentFile(upload.read(), name=f"{folder}/{uuid.uuid4().hex}.pdf")
    if b"<svg" in head.lower() or head.lstrip().startswith(b"<?xml"):
        return ContentFile(sanitise_svg(upload.read()), name=f"{folder}/{uuid.uuid4().hex}.svg")
    try:
        image = Image.open(upload)
        image_format = image.format
        image.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValidationError("Upload a JPG, PNG, WebP, SVG or PDF file.")
    if image_format not in RASTER_TYPES:
        raise ValidationError("Upload a JPG, PNG, WebP, SVG or PDF file.")
    upload.seek(0)
    return ContentFile(upload.read(), name=f"{folder}/{uuid.uuid4().hex}.{RASTER_TYPES[image_format]}")


# --------------------------------------------------------------------------
# Visitors and rate limits
# --------------------------------------------------------------------------


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def visitor_hash(request):
    """A salted digest of IP + user agent (ANL-01).

    Enough to tell a repeat scan from a fresh one; not enough to identify a
    person. Ghana's Data Protection Act treats an IP address as personal
    data, so the raw address is never stored.
    """
    raw = "|".join(
        [client_ip(request), request.META.get("HTTP_USER_AGENT", ""), settings.ANALYTICS_SALT]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def rate_limited(key, limit, window_seconds):
    """Count one hit against ``key``; True once the limit is exceeded (SEC-04)."""
    cache_key = f"rl:{key}"
    added = cache.add(cache_key, 1, window_seconds)
    if added:
        return False
    try:
        count = cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, window_seconds)
        return False
    return count > limit


def clear_rate_limit(key):
    cache.delete(f"rl:{key}")


def safe_next(target):
    """Only follow a relative URL on this site."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return None
