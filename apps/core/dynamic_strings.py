"""Wording that is kept in the database but shown to customers.

Plan taglines and delivery options are rows staff can edit, so they are not in the code where the
translation tool would find them. The wording the site launches with is listed here, once, so it is
translated like everything else; templates and forms look it up with ``{% trans plan.blurb %}`` or
``gettext(zone.name)``. Wording staff type later shows exactly as typed until a translation for it is
added to this list.

Keep these in step with ``seed_initial.py``.
"""

from django.utils.translation import gettext_noop as _

PLAN_TAGLINES = [
    _("Everything you need to share one card."),
    _("Your brand, your colours, and full analytics."),
    _("Your whole team under one brand. The bigger the team, the less each person costs."),
]

DELIVERY_OPTIONS = [
    _("Pickup from the AD Graphics office"),
    _("Collect in person. Free."),
    _("Ready the day your cards pass quality checks"),
    _("Delivery within Cape Coast"),
    _("Rider delivery to your door."),
    _("1 working day after dispatch"),
    _("Delivery to other regions of Ghana"),
    _("By courier to your nearest office or door."),
    _("2–4 working days after dispatch"),
    _("West Africa"),
    _("Nigeria, Côte d’Ivoire, Togo, Benin, Burkina Faso and neighbours."),
    _("3–7 working days after dispatch"),
    _("Rest of Africa"),
    _("International courier to the rest of Africa."),
    _("5–10 working days after dispatch"),
    _("UK and Europe"),
    _("International courier to the UK and Europe."),
    _("USA, Canada and rest of the world"),
    _("International courier anywhere else."),
    _("5–12 working days after dispatch"),
]

# Date patterns (Django's date-format letters), used by the ldate filter. Each language writes the pattern
# its readers expect, for example Chinese "Y年n月j日"; where a language agrees with English it keeps it.
DATE_PATTERNS = [
    _("j F Y"),
    _("j M Y"),
    _("j M Y, H:i"),
    _("j F Y, H:i"),
    _("j M, H:i"),
    _("d M"),
    _("M Y"),
]
