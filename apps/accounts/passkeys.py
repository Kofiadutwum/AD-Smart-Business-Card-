"""Face ID, fingerprint and device-PIN sign-in (passkeys, WebAuthn) for customers and staff.

The phone or computer checks the face or finger; the server only keeps each device's public key. For staff a passkey
replaces the emailed 6-digit code, because it already proves both the device and the person.
The webauthn library is imported inside the views, so a damaged install turns this feature off, not the site.
"""

import json
import logging
from urllib.parse import urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.core.models import AuditLog
from apps.core.utils import clear_rate_limit, client_ip, rate_limited, safe_next

from .models import Passkey

log = logging.getLogger(__name__)
BACKEND = "django.contrib.auth.backends.ModelBackend"
LOCAL_HOSTS = {"localhost", "127.0.0.1"}
DEVICE_NAMES = (
    ("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android phone"), ("Windows", "Windows PC"),
    ("Macintosh", "Mac"), ("CrOS", "Chromebook"), ("Linux", "Linux computer"),
)


def _library():
    try:
        import webauthn
        import webauthn.helpers.structs  # noqa: F401  (loads the option types used below)
    except Exception:  # ImportError, or OSError when a compiled file of cryptography is missing
        log.exception("Passkeys are off: the webauthn library did not load")
        return None
    return webauthn


def _error(message, status=400, **extra):
    return JsonResponse({"error": message, **extra}, status=status)


def _unavailable():
    return _error(_("Face ID and fingerprint sign-in is not available right now. Use your password."), 503)


def _relying_party(request):
    """The domain passkeys belong to, and the origin the browser reports. www and the bare domain share passkeys."""
    host = request.get_host()
    domain = host.split(":")[0]
    site = urlparse(settings.SITE_URL).hostname or ""
    rp_id = site if domain == site or domain.endswith(f".{site}") else domain
    scheme = "http" if domain in LOCAL_HOSTS else "https"
    return rp_id, f"{scheme}://{host}"


def _body(request):
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _device_name(request):
    agent = request.META.get("HTTP_USER_AGENT", "")
    return next((name for needle, name in DEVICE_NAMES if needle in agent), "Phone or computer")


def _may_add(request):
    """Signed in, and for staff past the emailed code, so a password alone can never add a way around the code."""
    user = request.user
    if not user.is_authenticated:
        return False
    return not (user.is_staff and settings.STAFF_TWO_FACTOR_REQUIRED and request.session.get("staff_2fa") != user.pk)


# --------------------------------------------------------------------------
# Signing in
# --------------------------------------------------------------------------


@require_POST
def sign_in_start(request):
    webauthn = _library()
    if webauthn is None:
        return _unavailable()
    structs = webauthn.helpers.structs
    rp_id, _origin = _relying_party(request)
    options = webauthn.generate_authentication_options(
        rp_id=rp_id, user_verification=structs.UserVerificationRequirement.REQUIRED,
    )
    request.session["passkey_sign_in"] = webauthn.helpers.bytes_to_base64url(options.challenge)
    return HttpResponse(webauthn.options_to_json(options), content_type="application/json")


@require_POST
def sign_in(request):
    webauthn = _library()
    if webauthn is None:
        return _unavailable()
    limit_key = f"passkey:{client_ip(request)}"
    if rate_limited(limit_key, 20, 15 * 60):
        return _error(_("Too many attempts. Wait 15 minutes, or use your password."), 429)
    challenge = request.session.pop("passkey_sign_in", None)
    if not challenge:
        return _error(_("That took too long. Try again."))
    data = _body(request)
    credential = data.get("credential") if isinstance(data.get("credential"), dict) else {}
    passkey = Passkey.objects.select_related("user").filter(credential_id=str(credential.get("id", ""))).first()
    if passkey is None:  # removed on the website but still on the device: the page stops offering it
        return _error(_("Face ID and fingerprint sign-in was turned off for this device. Use your password."), forget=True)
    rp_id, origin = _relying_party(request)
    try:
        verified = webauthn.verify_authentication_response(
            credential=credential, expected_challenge=webauthn.base64url_to_bytes(challenge),
            expected_rp_id=rp_id, expected_origin=origin, credential_public_key=bytes(passkey.public_key),
            credential_current_sign_count=passkey.sign_count, require_user_verification=True,
        )
    except Exception as exc:  # any malformed or forged answer is simply refused
        log.info("Passkey sign-in refused: %s", exc)
        return _error(_("That did not work. Try again, or use your password."))
    user = passkey.user
    if user.is_suspended:
        return _error(_("This account is suspended. Contact support."), 403)
    if not user.is_active:
        return _error(_("That did not work. Try again, or use your password."), 403)

    passkey.sign_count = verified.new_sign_count
    passkey.last_used_at = timezone.now()
    passkey.save(update_fields=["sign_count", "last_used_at"])
    clear_rate_limit(limit_key)
    login(request, user, backend=BACKEND)
    target = safe_next(data.get("next"))
    if user.is_staff:
        from apps.staff.views.auth import _start_staff_session  # imported here: the staff app imports this one

        _start_staff_session(request, user, two_factor_passed=True)  # the passkey stands in for the emailed code
        return JsonResponse({"redirect": target if target and target.startswith("/staff") else reverse("staff:home")})
    return JsonResponse({"redirect": target or reverse("dashboard:home")})


# --------------------------------------------------------------------------
# Turning it on and off for a device
# --------------------------------------------------------------------------


@require_POST
def add_start(request):
    if not _may_add(request):
        return _error(_("Sign in again first."), 403)
    webauthn = _library()
    if webauthn is None:
        return _unavailable()
    structs, user = webauthn.helpers.structs, request.user
    rp_id, _origin = _relying_party(request)
    options = webauthn.generate_registration_options(
        rp_id=rp_id, rp_name=settings.SITE_NAME, user_id=str(user.pk).encode(), user_name=user.email,
        user_display_name=user.full_name or user.email,
        exclude_credentials=[
            structs.PublicKeyCredentialDescriptor(id=webauthn.base64url_to_bytes(p.credential_id)) for p in user.passkeys.all()
        ],
        authenticator_selection=structs.AuthenticatorSelectionCriteria(
            authenticator_attachment=structs.AuthenticatorAttachment.PLATFORM,  # this device's own face, finger or PIN
            resident_key=structs.ResidentKeyRequirement.REQUIRED,  # so sign-in needs no email typed
            user_verification=structs.UserVerificationRequirement.REQUIRED,
        ),
    )
    request.session["passkey_add"] = webauthn.helpers.bytes_to_base64url(options.challenge)
    return HttpResponse(webauthn.options_to_json(options), content_type="application/json")


@require_POST
def add(request):
    if not _may_add(request):
        return _error(_("Sign in again first."), 403)
    webauthn = _library()
    if webauthn is None:
        return _unavailable()
    challenge = request.session.pop("passkey_add", None)
    if not challenge:
        return _error(_("That took too long. Try again."))
    rp_id, origin = _relying_party(request)
    credential = _body(request).get("credential")
    try:
        verified = webauthn.verify_registration_response(
            credential=credential if isinstance(credential, dict) else {},
            expected_challenge=webauthn.base64url_to_bytes(challenge), expected_rp_id=rp_id, expected_origin=origin,
            require_user_verification=True,
        )
    except Exception as exc:
        log.info("Passkey not added: %s", exc)
        return _error(_("That did not work. Try again."))
    credential_id = webauthn.helpers.bytes_to_base64url(verified.credential_id)
    if len(credential_id) > 255 or Passkey.objects.filter(credential_id=credential_id).exists():
        return _error(_("This device cannot be added. Try again."))
    name = _device_name(request)
    Passkey.objects.create(
        user=request.user, credential_id=credential_id, public_key=verified.credential_public_key,
        sign_count=verified.sign_count, name=name,
    )
    AuditLog.record(request.user, "passkey_added", request.user, device=name)
    messages.success(request, _("Face ID and fingerprint sign-in is on for this device."))
    return JsonResponse({"credential": credential_id})


@login_required
@require_POST
def remove(request, pk):
    passkey = get_object_or_404(Passkey, pk=pk, user=request.user)
    passkey.delete()
    AuditLog.record(request.user, "passkey_removed", request.user, device=passkey.name)
    messages.success(request, _("%(device)s no longer signs you in with Face ID or fingerprint.") % {"device": passkey.name})
    return redirect(safe_next(request.POST.get("next")) or "dashboard:settings")
