import time
from datetime import date

from django.conf import settings
from django.utils.functional import SimpleLazyObject

from .models import SiteSettings


def _shell(user):
    """Values the dashboard shell needs on every signed-in customer page."""
    from apps.billing.services import plan_for_limits

    return {
        "shell_subscription": SimpleLazyObject(lambda: user.subscription),
        "shell_plan": SimpleLazyObject(lambda: plan_for_limits(user)),
        "shell_cards": SimpleLazyObject(lambda: list(user.cards.filter(deleted_at__isnull=True).order_by("created_at"))),
    }


def site(request):
    user = getattr(request, "user", None)
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
        # Development only: defeat the browser cache for CSS/JS. Production
        # uses hashed filenames from ManifestStaticFilesStorage instead.
        "ASSET_Q": f"?v={int(time.time())}" if settings.DEBUG else "",
    }
