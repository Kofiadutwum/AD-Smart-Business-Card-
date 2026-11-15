"""Which country a visitor is in, worked out from their internet address.

The lookup uses a database kept on our own server (MaxMind GeoLite2 country data, packed by geoip2fast),
so no visitor address is sent to another company. It is loaded once, on first use. An address the database
does not know counts as "not Ghana".

This product includes GeoLite2 data created by MaxMind, available from https://www.maxmind.com.
"""

import ipaddress
import logging
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger(__name__)

GHANA = "GH"


@lru_cache(maxsize=1)
def _database():
    from geoip2fast import GeoIP2Fast

    return GeoIP2Fast(geoip2fast_data_file="geoip2fast-ipv6.dat.gz", verbose=False)


def visitor_ip(request):
    """The visitor's own address.

    A direct connection from a public address wins, so nobody can pick their country by sending an
    X-Forwarded-For header. Behind a local proxy (a private address) that header is used instead.
    """
    remote = request.META.get("REMOTE_ADDR", "")
    try:
        if ipaddress.ip_address(remote).is_global:
            return remote
    except ValueError:
        pass
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded.split(",")[0].strip() if forwarded else remote


def country_of(ip):
    """Two-letter country code for an address, or "" when it is private, invalid or not in the database."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    if not address.is_global:
        return ""
    try:
        code = _database().lookup(str(address)).country_code
    except Exception:  # a broken lookup must never break a page: the visitor just sees the standard prices
        logger.exception("Country lookup failed")
        return ""
    return code if len(code) == 2 and code.isalpha() else ""


def country(request):
    """The visitor's country code ("" when unknown), worked out once per request."""
    if not hasattr(request, "_country"):
        request._country = (settings.GEO_FORCE_COUNTRY or country_of(visitor_ip(request))).upper()
    return request._country


def in_ghana(request):
    return country(request) == GHANA
