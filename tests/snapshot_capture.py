"""Capture how the customer-facing pages render, so a change that should not alter them can be checked.

Run by hand (the file name keeps it out of the normal test run):

    SNAP_DIR=/tmp/before .venv/Scripts/python.exe manage.py test tests.snapshot_capture

Every page is written as normalised HTML: CSRF tokens, timestamps and asset query strings are
replaced, whitespace is collapsed. Capture before and after a change and diff the two folders.
"""

import os
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.test import override_settings

from apps.billing.models import ExchangeRate
from apps.cards.models import CardEvent, CardPhone, Lead, SocialLink

from .helpers import BaseTest

FIXED = datetime(2026, 10, 5, 12, 0, tzinfo=dt_timezone.utc)

PUBLIC = [
    "/", "/pricing", "/nfc-cards", "/faq", "/terms", "/privacy", "/refunds", "/contact",
    "/auth/register", "/auth/login", "/auth/forgot-password", "/does-not-exist",
]
CUSTOMER = [
    "/dashboard/", "/dashboard/card/", "/dashboard/cards/", "/dashboard/links/", "/dashboard/share/",
    "/dashboard/analytics/", "/dashboard/contacts/", "/dashboard/nfc/", "/dashboard/settings/",
    "/billing/", "/billing/receipts", "/nfc/order", "/support/", "/contact", "/auth/password",
]


def normalise(html):
    html = re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', 'name="csrfmiddlewaretoken" value="X"', html)
    html = re.sub(r"(\.(?:css|js|webp|png|jpg|svg|woff2))\?v=\d+", r"", html)
    html = re.sub(r"/c/[a-z0-9]{8}/", "/c/SLUG/", html)
    html = re.sub(r'<html lang="[^"]*"', '<html lang="L"', html)
    html = re.sub(r">\s+<", "><", html)
    html = re.sub(r"\s+", " ", html)
    return html.strip()


# SNAP_LANG asks for the pages in that language (as a phone would, with Accept-Language), and SNAP_LOCALE
# points at a folder of catalogs to use instead of locale/ (for example a pseudo-translation).
EXTRA = {"LOCALE_PATHS": [os.environ["SNAP_LOCALE"]]} if os.environ.get("SNAP_LOCALE") else {}


@override_settings(DEBUG=False, **EXTRA)
class SnapshotCapture(BaseTest):
    def test_capture(self):
        out = Path(os.environ["SNAP_DIR"])
        out.mkdir(parents=True, exist_ok=True)
        if os.environ.get("SNAP_LANG"):
            self.client.defaults["HTTP_ACCEPT_LANGUAGE"] = os.environ["SNAP_LANG"]
        with mock.patch("django.utils.timezone.now", return_value=FIXED):
            ExchangeRate.objects.create(
                ghs_per_usd=Decimal("11.5"), provider="test", fetched_at=FIXED,
                other_rates={"GBP": "0.75", "EUR": "0.86", "CNY": "7.2", "NGN": "1520", "XOF": "565"},
            )
            user = self.make_user()
            self.subscribe(user, self.pro, days=200)
            card = self.make_card(user, job_title="Sales Manager", business_name="Mensah Trading", email="kwame@example.com", bio="I help businesses grow.")
            CardPhone.objects.create(card=card, number="+233244123456", label="MTN", position=0)
            SocialLink.objects.create(card=card, platform="linkedin", url="https://linkedin.com/in/kwame", position=0)
            Lead.objects.create(card=card, name="Efua Owusu", phone="0245000000", note="Call me", created_at=FIXED - timedelta(days=1))
            CardEvent.objects.create(card=card, kind=CardEvent.VIEW, source="qr", visitor_hash="a", occurred_at=FIXED - timedelta(days=1))

            def save(name, response):
                body = response.content.decode("utf-8", "replace")
                label = name.strip("/").replace("/", "_") or "home"
                (out / f"{label}.html").write_text(f"STATUS {response.status_code}\n{normalise(body)}\n", encoding="utf-8")

            for path in PUBLIC:
                save("public_" + path, self.client.get(path))
            save("card_public", self.client.get(f"/c/{card.slug}"))
            save("card_unknown", self.client.get("/c/nobody-here"))

            self.client.force_login(user)
            for path in CUSTOMER:
                save("customer_" + path, self.client.get(path))
            self.client.logout()

            unverified = self.make_user(email="new@example.com", verified=False, phone="+233200000000")
            self.client.force_login(unverified)
            save("customer_verify", self.client.get("/auth/verify"))
        self.assertTrue(any(out.iterdir()))
