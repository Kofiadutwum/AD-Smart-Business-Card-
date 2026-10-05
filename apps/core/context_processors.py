import json
import time
from datetime import date

from django.conf import settings
from django.utils.functional import SimpleLazyObject, lazy

from . import currency
from .jsstrings import strings_json
from .models import SiteSettings


def _shell(user):
    """Values the dashboard shell needs on every signed-in customer page."""
    from apps.billing.services import plan_for_limits

    return {
        "shell_subscription": SimpleLazyObject(lambda: user.subscription),
        "shell_plan": SimpleLazyObject(lambda: plan_for_limits(user)),
        "shell_cards": SimpleLazyObject(lambda: list(user.cards.filter(deleted_at__isnull=True).order_by("created_at"))),
    }


def _latest_rate():
    from apps.billing.fx import latest_rate

    return latest_rate()


def _currency_json(chosen):
    today = _latest_rate()
    return json.dumps(currency.config_for(currency.effective(chosen, today), today))


def site(request):
    user = getattr(request, "user", None)
    chosen = getattr(request, "currency", currency.DEFAULT)
    extra = {}
    if user is not None and user.is_authenticated:
        if user.is_staff:
            from apps.staff.permissions import Perms

            extra = {"staff_perms": Perms(user)}
        else:
            extra = _shell(user)
    return extra | {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_SHORT_NAME": settings.SITE_SHORT_NAME,
        "SITE_URL": settings.SITE_URL,
        "site": SiteSettings.load(),
        "current_year": date.today().year,
        "payment_sandbox": settings.PAYMENT_SANDBOX,
        # The currency the visitor picked, the choices for the picker, and the price-calculator settings.
        "currency": chosen,
        "currency_choices": [(code, info["symbol"].strip() or code, info["name"]) for code, info in currency.CURRENCIES.items()],
        "currency_json": lazy(_currency_json, str)(chosen),
        "js_strings_json": strings_json(),
        # Today's USD rate for the {% money %} tag; looked up once, on first use.
        "fx_rate": SimpleLazyObject(_latest_rate),
        # Development only: defeat the browser cache for CSS/JS. Production
        # uses hashed filenames from ManifestStaticFilesStorage instead.
        "ASSET_Q": f"?v={int(time.time())}" if settings.DEBUG else "",
    }
