"""Email verification, recovery codes and Google sign-in."""

import json
import secrets
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core import signing
from django.urls import reverse
from django.utils import timezone

from apps.core.emails import send_email

from .models import OneTimeCode

VERIFY_SALT = "accounts.verify-email"
VERIFY_MAX_AGE = 60 * 60 * 48
RESET_CODE_MINUTES = 15  # REG-06
RESET_COOLDOWN_SECONDS = 60


def send_verification_email(user):
    token = signing.dumps({"uid": user.pk, "email": user.email}, salt=VERIFY_SALT)
    link = settings.SITE_URL + reverse("accounts:verify_email", args=[token])
    return send_email(
        to=user.email,
        subject="Confirm your email address",
        template="verify_email",
        context={"user": user, "link": link},
        user=user,
        kind="verify_email",
    )


def read_verification_token(token):
    try:
        data = signing.loads(token, salt=VERIFY_SALT, max_age=VERIFY_MAX_AGE)
    except signing.BadSignature:
        return None
    return data


def issue_reset_code(user):
    """Create and email a 6-digit code. Returns False during the cooldown."""
    latest = user.one_time_codes.filter(purpose=OneTimeCode.PURPOSE_PASSWORD_RESET).first()
    if latest and timezone.now() - latest.created_at < timedelta(seconds=RESET_COOLDOWN_SECONDS):
        return False
    # Only the newest code works.
    user.one_time_codes.filter(purpose=OneTimeCode.PURPOSE_PASSWORD_RESET, used_at__isnull=True).update(
        used_at=timezone.now()
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    otp = OneTimeCode(
        user=user,
        purpose=OneTimeCode.PURPOSE_PASSWORD_RESET,
        expires_at=timezone.now() + timedelta(minutes=RESET_CODE_MINUTES),
    )
    otp.set_code(code)
    otp.save()
    send_email(
        to=user.email,
        subject=f"Your password reset code: {code}",
        template="password_reset_code",
        context={"user": user, "code": code, "minutes": RESET_CODE_MINUTES},
        user=user,
        kind="password_reset_code",
    )
    return True


def anonymise_user(user, reason=""):
    """Remove personal data but keep financial records (PRV-04, EXP-06).

    Card slugs are retired, never released, so an old printed card can
    never open a stranger's details (URL-06).
    """
    from django.db import transaction

    from apps.cards.models import CardEvent, CardSlug, Lead
    from apps.core.models import AuditLog

    with transaction.atomic():
        for card in user.cards.all():
            if card.avatar:
                card.avatar.delete(save=False)
            if card.logo:
                card.logo.delete(save=False)
            if card.background:
                card.background.delete(save=False)
            card.full_name = "Deleted card"
            card.business_name = card.job_title = card.bio = ""
            card.whatsapp = card.email = card.website = card.address = card.ghana_post_gps = ""
            card.latitude = card.longitude = None
            card.avatar = card.logo = card.background = ""
            card.deleted_at = card.deleted_at or timezone.now()
            card.save()
            card.phones.all().delete()
            card.social_links.all().delete()
            Lead.objects.filter(card=card).delete()
            CardEvent.objects.filter(card=card).update(visitor_hash="")
            CardSlug.objects.filter(card=card).update(retired_at=timezone.now())
        user.email = f"deleted-{user.pk}@deleted.invalid"
        user.full_name = ""
        user.phone = None
        user.google_sub = None
        user.company_name = user.company_address = ""
        user.is_active = False
        user.set_unusable_password()
        user.anonymised_at = timezone.now()
        user.save()
        AuditLog.record(None, "account_anonymised", user, reason=reason)


# --------------------------------------------------------------------------
# Google OAuth (kept from the Flask site; callback path unchanged)
# --------------------------------------------------------------------------

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"


def google_configured():
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def google_redirect_uri():
    return settings.SITE_URL + reverse("accounts:google_callback")


def google_authorization_url(state):
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH}?{urlencode(params)}"


def _fetch_json(request):
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def google_user_from_code(code):
    """Return (profile dict, error message)."""
    payload = urlencode(
        {
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": google_redirect_uri(),
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    try:
        token = _fetch_json(
            Request(GOOGLE_TOKEN, data=payload, method="POST",
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
        )
        access = token.get("access_token")
        if not access:
            return None, "Google did not return an access token."
        info = _fetch_json(
            Request(GOOGLE_USERINFO, headers={"Authorization": f"Bearer {access}", "Accept": "application/json"})
        )
    except HTTPError:
        return None, "Google sign-in failed. Try again."
    except (URLError, OSError, ValueError):
        return None, "Google could not be reached. Try again."

    sub = str(info.get("sub") or "").strip()
    email = str(info.get("email") or "").strip().lower()
    if not sub or not email:
        return None, "Google did not share your account details."
    if info.get("email_verified") is not True:
        return None, "Your Google email address must be verified first."
    name = str(info.get("name") or "").strip() or " ".join(
        p for p in (info.get("given_name"), info.get("family_name")) if p
    )
    return {"sub": sub, "email": email, "name": name or "Card User"}, None
