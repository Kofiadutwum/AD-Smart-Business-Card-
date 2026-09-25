"""USD display prices (CUR-01..05).

The customer is always charged in GHS. The USD figure is an approximation
from the latest stored rate. If the newest rate is older than the fallback
window (72 hours by default) the USD figure is hidden entirely.
"""

import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.core.models import SiteSettings

from .models import ExchangeRate

logger = logging.getLogger(__name__)


def latest_rate():
    """The newest usable rate, or None when it is too old to show (CUR-04)."""
    rate = cache.get("fx:latest")
    if rate is None:
        rate = ExchangeRate.objects.order_by("-fetched_at").first() or False
        cache.set("fx:latest", rate, 600)
    if not rate:
        return None
    window = timedelta(hours=SiteSettings.load().fx_fallback_hours)
    if timezone.now() - rate.fetched_at > window:
        return None
    return rate


def refresh_rate():
    """Fetch today's rate from the provider and store it (CUR-02, CUR-03)."""
    try:
        response = requests.get(settings.FX_PROVIDER_URL, timeout=15)
        response.raise_for_status()
        data = response.json()
        value = Decimal(str(data["rates"]["GHS"]))
    except (requests.RequestException, KeyError, ValueError, InvalidOperation) as exc:
        logger.warning("Exchange rate refresh failed: %s", exc)
        return None
    if value <= 0:
        return None
    rate = ExchangeRate.objects.create(ghs_per_usd=value, provider="open.er-api.com")
    cache.delete("fx:latest")
    return rate


def usd_value(minor, rate):
    if not rate:
        return None
    return (Decimal(int(minor)) / 100 / rate.ghs_per_usd).quantize(Decimal("0.01"))
