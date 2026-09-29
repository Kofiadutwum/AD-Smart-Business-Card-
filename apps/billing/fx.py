"""USD display prices (CUR-01..05).

The customer is always charged in GHS. The USD figure is an approximation
from the newest stored rate. When the provider cannot be reached, the last
rate it gave stays in use until a newer one arrives, so dollar prices never
disappear. The daily job fetches a fresh rate each morning, and a rate more
than a day old is also refreshed in the background (at most once an hour).
"""

import logging
import threading
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.utils import timezone

from .models import ExchangeRate

logger = logging.getLogger(__name__)

STALE_AFTER = timedelta(hours=24)
RETRY_EVERY_SECONDS = 60 * 60


def latest_rate():
    """The newest stored rate, however old; None only before the first one."""
    rate = cache.get("fx:latest")
    if rate is None:
        rate = ExchangeRate.objects.order_by("-fetched_at").first() or False
        cache.set("fx:latest", rate, 600)
    if not rate or timezone.now() - rate.fetched_at > STALE_AFTER:
        _refresh_in_background()
    return rate or None


def _refresh_in_background():
    """Fetch a new rate without holding up the page that noticed the old one."""
    if not settings.FX_AUTO_REFRESH or not cache.add("fx:refreshing", 1, RETRY_EVERY_SECONDS):
        return

    def run():
        try:
            refresh_rate()
        finally:
            connections.close_all()

    threading.Thread(target=run, name="fx-refresh", daemon=True).start()


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
