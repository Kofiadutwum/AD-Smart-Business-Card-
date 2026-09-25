"""Staff sign-in with two-factor authentication (SEC-03, Figure 8A)."""

import time

import pyotp
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.cards.services import qr_svg
from apps.core.models import AuditLog
from apps.core.utils import clear_rate_limit, client_ip, rate_limited, safe_next

from ..forms import StaffLoginForm, TotpForm

BACKEND = "django.contrib.auth.backends.ModelBackend"


def staff_login(request):
    if request.user.is_authenticated and request.user.is_staff:
        return redirect("staff:two_factor")
    form = StaffLoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].lower()
        key = f"staff-login:{client_ip(request)}:{email}"
        if rate_limited(key, 5, 15 * 60):
            messages.error(request, "Too many attempts. Wait 15 minutes.")
        else:
            user = User.objects.filter(email__iexact=email, is_staff=True, is_active=True).first()
            if user and user.check_password(form.cleaned_data["password"]):
                clear_rate_limit(key)
                login(request, user, backend=BACKEND)
                request.session.pop("staff_2fa", None)
                return redirect(f"/staff/2fa?next={safe_next(request.GET.get('next')) or ''}")
            messages.error(request, "Those details do not match a staff account.")
    return render(request, "staff/login.html", {"form": form})


def two_factor(request):
    user = request.user
    if not user.is_authenticated or not user.is_staff:
        return redirect("staff:login")
    next_url = safe_next(request.GET.get("next")) or "/staff/"
    enrolling = not user.has_totp
    if enrolling:
        secret = request.session.get("totp_enrol") or pyotp.random_base32()
        request.session["totp_enrol"] = secret
    else:
        secret = user.totp_secret

    form = TotpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if rate_limited(f"totp:{user.pk}", 6, 10 * 60):
            messages.error(request, "Too many codes tried. Wait 10 minutes.")
        elif pyotp.TOTP(secret).verify(form.cleaned_data["code"].strip(), valid_window=1):
            clear_rate_limit(f"totp:{user.pk}")
            if enrolling:
                user.totp_secret = secret
                user.totp_confirmed_at = timezone.now()
                user.save(update_fields=["totp_secret", "totp_confirmed_at"])
                request.session.pop("totp_enrol", None)
                AuditLog.record(user, "staff_2fa_enrolled", user)
            request.session["staff_2fa"] = user.pk
            request.session["staff_seen"] = time.time()
            request.session.set_expiry(settings.STAFF_IDLE_TIMEOUT_SECONDS * 16)
            AuditLog.record(user, "staff_login", user)
            return redirect(next_url)
        else:
            messages.error(request, "That code is not right. Check the time on your phone and try again.")

    context = {"form": form, "enrolling": enrolling}
    if enrolling:
        uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="AD Smart Staff")
        context.update({"qr": qr_svg(uri, scale=5), "secret": secret})
    return render(request, "staff/two_factor.html", context)


@require_POST
def staff_logout(request):
    logout(request)
    messages.info(request, "Signed out of the staff area.")
    return redirect("staff:login")
