"""Paystack adapter: hosted checkout, verification, webhooks and refunds.

Customers enter card and Mobile Money details only on Paystack's hosted
checkout (PAY-01). This module never sees them.

With PAYMENT_SANDBOX on (always, when no secret key is set) every call is
simulated, so the whole purchase flow can be demonstrated without a gateway.
"""

import hashlib
import hmac
import json
import logging
import secrets
import time
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)


class PaymentError(Exception):
    """Paystack refused a request or could not be reached."""


def new_reference(prefix="ADS"):
    return f"{prefix}-{int(time.time())}-{secrets.token_hex(4).upper()}"


def _headers():
    return {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def _call(method, path, payload=None):
    url = settings.PAYSTACK_BASE_URL + path
    try:
        response = requests.request(method, url, headers=_headers(), json=payload, timeout=30)
    except requests.RequestException as exc:
        logger.warning("Paystack %s %s unreachable: %s", method, path, exc)
        raise PaymentError(_("The payment gateway could not be reached. Try again in a moment.")) from exc
    try:
        body = response.json() if response.content else {}
    except ValueError:
        body = {}
    if response.status_code >= 400 or not body.get("status"):
        logger.warning("Paystack %s %s -> HTTP %s %s", method, path, response.status_code, body.get("message"))
        raise PaymentError(body.get("message") or "The payment gateway declined this request.")
    return body.get("data") or {}


def initialize(*, email, amount_minor, reference, callback_url, metadata=None, channels=None):
    """Start a hosted checkout and return the URL to send the customer to.

    ``channels`` narrows the page to card or Mobile Money; without it both are offered.
    """
    if settings.PAYMENT_SANDBOX:
        return {
            "authorization_url": f"{callback_url}?{urlencode({'reference': reference, 'sandbox': 1})}",
            "reference": reference,
        }
    return _call(
        "POST",
        "/transaction/initialize",
        {
            "email": email,
            "amount": amount_minor,
            "currency": settings.CURRENCY,
            "reference": reference,
            "callback_url": callback_url,
            "channels": list(channels or ["card", "mobile_money"]),
            "metadata": metadata or {},
        },
    )


def verify(reference, *, expected_amount=None, expected_currency=None):
    """Ask Paystack what really happened to a transaction (PAY-05)."""
    if settings.PAYMENT_SANDBOX:
        outcome = settings.PAYMENT_SANDBOX_OUTCOME
        if outcome not in {"success", "failed", "abandoned"}:
            outcome = "success"
        return {
            "status": outcome,
            "reference": reference,
            "amount": expected_amount,
            "currency": expected_currency or settings.CURRENCY,
            "channel": "sandbox",
            "fees": 0,
            "paid_at": None,
            # What Paystack returns for a card payment: a token that can be charged again later.
            "authorization": {
                "authorization_code": f"AUTH_sandbox_{reference[-8:]}", "reusable": True, "channel": "card",
                "brand": "visa", "last4": "4081", "exp_month": "12", "exp_year": "2030",
            },
        }
    return _call("GET", f"/transaction/verify/{reference}")


def charge_authorization(*, email, amount_minor, reference, authorization_code, metadata=None):
    """Charge a card the customer has already paid with (automatic renewal). Returns Paystack's result;
    a decline comes back as a status other than "success" or as a PaymentError."""
    if settings.PAYMENT_SANDBOX:
        outcome = "failed" if settings.PAYMENT_SANDBOX_OUTCOME == "failed" else "success"
        return {"status": outcome, "reference": reference, "amount": amount_minor}
    return _call(
        "POST",
        "/transaction/charge_authorization",
        {
            "authorization_code": authorization_code,
            "email": email,
            "amount": amount_minor,
            "currency": settings.CURRENCY,
            "reference": reference,
            "metadata": metadata or {},
        },
    )


def refund(reference, amount_minor):
    if settings.PAYMENT_SANDBOX:
        return {"id": f"sandbox-refund-{secrets.token_hex(3)}", "status": "processed"}
    return _call("POST", "/refund", {"transaction": reference, "amount": amount_minor})


def signature_is_valid(raw_body, signature):
    """PAY-06: HMAC-SHA512 of the raw body with the secret key."""
    secret = (settings.PAYSTACK_SECRET_KEY or "").encode("utf-8")
    if not secret or not signature:
        return False
    expected = hmac.new(secret, raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse_event(raw_body):
    try:
        return json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise PaymentError(_("Malformed webhook payload.")) from exc
