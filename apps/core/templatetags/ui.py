"""Template helpers available in every template (registered as builtins)."""

from decimal import ROUND_HALF_UP, Decimal

from django import template
from django.templatetags.static import static
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html

from apps.core.utils import display_phone, format_ghs, whatsapp_digits

register = template.Library()


@register.simple_tag
def icon(name, size=20, css="", label=""):
    """Inline an icon from the sprite. Decorative unless a label is given."""
    sprite = static("icons/sprite.svg")
    ref = name if name.startswith(("i-", "b-")) else f"i-{name}"
    if label:
        return format_html(
            '<svg class="icon {}" width="{}" height="{}" role="img" aria-label="{}">'
            '<use href="{}#{}"></use></svg>',
            css, size, size, label, sprite, ref,
        )
    return format_html(
        '<svg class="icon {}" width="{}" height="{}" aria-hidden="true" focusable="false">'
        '<use href="{}#{}"></use></svg>',
        css, size, size, sprite, ref,
    )


@register.simple_tag
def brand_icon(name, size=20, css=""):
    return icon(f"b-{name}", size=size, css=css)


@register.filter
def ghs(minor):
    return format_ghs(minor)


@register.filter
def usd(minor, rate):
    """Approximate USD for a GHS minor amount; '' when no usable rate (CUR-04)."""
    if not rate:
        return ""
    value = (Decimal(int(minor or 0)) / 100 / Decimal(str(rate))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    if value == value.to_integral():
        return f"${value:,.0f}"
    return f"${value:,.2f}"


@register.filter
def phone(value):
    return display_phone(value)


@register.filter
def wa_digits(value):
    return whatsapp_digits(value)


@register.filter
def card_shows(card, field):
    """False when the owner hid this field from the public card (PRV-01)."""
    return field not in (getattr(card, "hidden_fields", None) or [])


@register.filter
def widget_type(bound_field):
    return bound_field.field.widget.__class__.__name__.lower()


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.inclusion_tag("components/hover_button.html")
def hover_button(text, href="", variant="", type="submit", name="", value="", css="", icon_name="arrow-right", attrs=""):
    """The interactive hover button (see static/css/components.css, .ihb)."""
    return {
        "text": text,
        "href": href,
        "variant": variant,
        "type": type,
        "name": name,
        "value": value,
        "css": css,
        "icon_name": icon_name,
        "attrs": attrs,
    }


@register.simple_tag(takes_context=True)
def nav_current(context, url_name, prefix=False):
    """aria-current for the active navigation item."""
    request = context.get("request")
    if request is None:
        return ""
    try:
        target = reverse(url_name)
    except NoReverseMatch:
        return ""
    path = request.path
    if path == target or (prefix and path.startswith(target) and target != "/"):
        return format_html('aria-current="page"')
    return ""


@register.simple_tag(takes_context=True)
def query_with(context, **kwargs):
    """Rebuild the current querystring with some values replaced."""
    request = context["request"]
    params = request.GET.copy()
    for key, value in kwargs.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"
