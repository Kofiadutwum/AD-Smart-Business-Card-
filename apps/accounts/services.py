"""Recovery codes, the live email check and Google sign-in."""

import difflib
import json
import secrets
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.emails import send_email, subject_text

from .models import OneTimeCode

RESET_CODE_MINUTES = 15  # REG-06
RESET_COOLDOWN_SECONDS = 60


COMMON_MAIL_DOMAINS = (
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "ymail.com", "outlook.com", "hotmail.com",
    "live.com", "msn.com", "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "mail.com",
)


def _domain_can_receive_mail(domain):
    """False only when DNS says nothing at this domain can take mail. A slow or broken lookup never blocks anyone."""
    import dns.exception
    import dns.resolver

    key = f"mailhost:{domain}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        try:
            answers = dns.resolver.resolve(domain, "MX", lifetime=3)
            found = any(str(record.exchange).strip(".") for record in answers)  # a lone "." is a null MX: no mail
        except dns.resolver.NoAnswer:  # no MX record: mail goes to the domain's own address, if it has one
            try:
                dns.resolver.resolve(domain, "A", lifetime=3)
                found = True
            except dns.resolver.NoAnswer:
                found = False
    except dns.resolver.NXDOMAIN:
        found = False
    except dns.exception.DNSException:
        return True
    cache.set(key, found, 3600)
    return found


def email_problem(raw, current_user=None):
    """Why this email address cannot be used, as a sentence for the customer, or None when it can.

    Used by sign-up, card setup, change-email and the live check beside those boxes, so they always agree.
    ``current_user`` is the signed-in customer changing their own address.
    """
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    from .models import User

    email = (raw or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        return _("That does not look like an email address.")
    if current_user and email == current_user.email.lower():
        return _("That is already your email address.")
    taken = User.objects.filter(email__iexact=email)
    if current_user:
        taken = taken.exclude(pk=current_user.pk)
    if taken.exists():
        return _("That email belongs to another account.") if current_user else _("That email already has an account. Sign in instead.")
    domain = email.rpartition("@")[2]
    if settings.EMAIL_DOMAIN_CHECK and not _domain_can_receive_mail(domain):
        return _("We could not find a mail server for %(domain)s. Check the spelling after the @.") % {"domain": domain}
    return None


def email_suggestion(raw):
    """A likely fix for a mistyped popular domain (gmial.com), or None. A hint only: it never blocks."""
    local, _at, domain = (raw or "").strip().lower().rpartition("@")
    if not local or domain in COMMON_MAIL_DOMAINS:
        return None
    close = difflib.get_close_matches(domain, COMMON_MAIL_DOMAINS, n=1, cutoff=0.8)
    return f"{local}@{close[0]}" if close else None


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
        subject=subject_text("Your password reset code: %(code)s", code=code),
        template="password_reset_code",
        context={"user": user, "code": code, "minutes": RESET_CODE_MINUTES},
        user=user,
        kind="password_reset_code",
    )
    return True


TEMP_PASSWORD_HOURS = 48
TEMP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I: staff read it out over the phone


def staff_reset_password(staff, customer, reason):
    """Give a customer who cannot use emailed recovery a temporary password, and return it for staff to read out once.

    The customer must choose their own at first sign-in and the temporary one stops after 48 hours. Changing the
    password signs out every session they have open, and any emailed reset code stops working. The email on file
    gets a notice (never the password), so the real owner of that address can spot a hijack.
    """
    from django.db import transaction

    from apps.core.models import AuditLog

    raw = "".join(secrets.choice(TEMP_ALPHABET) for _ in range(10))
    temporary = f"{raw[:5]}-{raw[5:]}"
    with transaction.atomic():
        customer.set_password(temporary)
        customer.password_temp_until = timezone.now() + timedelta(hours=TEMP_PASSWORD_HOURS)
        customer.save(update_fields=["password", "password_temp_until"])
        customer.one_time_codes.filter(purpose=OneTimeCode.PURPOSE_PASSWORD_RESET, used_at__isnull=True).update(
            used_at=timezone.now()
        )
        AuditLog.record(staff, "customer_password_reset", customer, reason=reason)
    send_email(
        to=customer.email,
        subject=subject_text("Our support team reset your AD Smart password"),
        template="password_reset_by_staff",
        context={
            "user": customer, "hours": TEMP_PASSWORD_HOURS,
            "link": settings.SITE_URL + reverse("accounts:one_time_password"),
        },
        user=customer,
        kind="password_reset_by_staff",
    )
    return temporary


def staff_change_email(staff, customer, new_email, reason):
    """Support changes the email of a customer who lost access to the old one. The old address gets a notice."""
    from django.db import transaction

    from apps.core.models import AuditLog

    old = customer.email
    with transaction.atomic():
        customer.email = new_email
        customer.save(update_fields=["email"])
        AuditLog.record(staff, "customer_email_changed", customer, reason=reason, old=old, new=new_email)
    send_email(
        to=old, subject=subject_text("The email on your AD Smart account was changed"), template="email_changed",
        context={"user": customer, "new": new_email}, user=customer, kind="email_changed",
    )


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
