"""Text the browser scripts show, translated into the visitor's language.

The page carries these as JSON (``<script id="i18n-strings">``) and the scripts look
a sentence up by its key, so every language is covered without a separate script.
"""

import json

from django.utils.functional import lazy
from django.utils.translation import gettext_lazy as _

STRINGS = {
    "theme_system": _("Theme: match device"),
    "theme_light": _("Theme: light"),
    "theme_dark": _("Theme: dark"),
    "theme_change": _("%(label)s. Change theme"),
    "copied": _("Copied"),
    "each_with": _("%(price)s each (%(cedis)s)"),
    "each": _("%(price)s each"),
    "per_person_year": _("%(price)s per person a year"),
    "your_name": _("Your name"),
    "locate_finding": _("Finding you…"),
    "locate_added": _("Location added. Save to keep it."),
    "locate_failed": _("We could not get your location. Check your browser’s permission."),
    "locate_cleared": _("Map pin removed. Save to keep it."),
    "featured": _("Featured"),
    "nfc_card_one": _("%(count)d NFC card"),
    "nfc_card_many": _("%(count)d NFC cards"),
    "free": _("Free"),
    "delivery": _("Delivery"),
    "shipping_delivery": _("Shipping and delivery"),
}


def _as_json():
    return json.dumps({key: str(value) for key, value in STRINGS.items()})


# Worked out only when a page actually prints it, in the language of that request.
strings_json = lazy(_as_json, str)
