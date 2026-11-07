"""Staff sign-in (SEC-03, Figure 8A): email and password, plus a 6-digit code emailed to the staff member when
STAFF_TWO_FACTOR_REQUIRED is on."""

import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import OneTimeCode, User
from apps.accounts.services import STAFF_CODE_MINUTES, issue_staff_code
from apps.core.models import AuditLog
from apps.core.utils import clear_rate_limit, client_ip, rate_limited, safe_next

from ..forms import StaffCodeForm, StaffLoginForm

BACKEND = "django.contrib.auth.backends.ModelBackend"


def _start_staff_session(request, user, two_factor_passed):
    """The session of a signed-in staff member: idle clock, long cookie, audit entry.

    ``staff_2fa`` is only set when the code was really checked, so switching STAFF_TWO_FACTOR_REQUIRED on later
    asks anyone already signed in for their code instead of trusting their old session.
    """
    if two_factor_passed:
        request.session["staff_2fa"] = user.pk
    request.session["staff_seen"] = time.time()
    request.session.set_expiry(settings.STAFF_IDLE_TIMEOUT_SECONDS * 16)
    AuditLog.record(user, "staff_login", user)


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
                next_url = safe_next(request.GET.get("next"))
                if settings.STAFF_TWO_FACTOR_REQUIRED:
                    return redirect(f"/staff/2fa?next={next_url or ''}")
                _start_staff_session(request, user, two_factor_passed=False)
                return redirect(next_url or "/staff/")
            messages.error(request, "Those details do not match a staff account.")
    return render(request, "staff/login.html", {"form": form, "two_factor_required": settings.STAFF_TWO_FACTOR_REQUIRED})


def two_factor(request):
    user = request.user
    if not user.is_authenticated or not user.is_staff:
        return redirect("staff:login")
    next_url = safe_next(request.GET.get("next")) or "/staff/"
    if not settings.STAFF_TWO_FACTOR_REQUIRED:
        # Staff who signed in through the customer form or Google land here; with the check off they are simply in.
        if "staff_seen" not in request.session:
            _start_staff_session(request, user, two_factor_passed=False)
        return redirect(next_url)
    if request.session.get("staff_2fa") == user.pk:
        return redirect(next_url)

    form = StaffCodeForm(request.POST or None)
    if request.method == "POST" and request.POST.get("action") == "resend":
        if issue_staff_code(user):
            messages.info(request, "A new code is on its way.")
        else:
            messages.info(request, "A code was sent a moment ago. Check your inbox, then try again in a minute.")
        return redirect(request.get_full_path())
    if request.method == "POST" and form.is_valid():
        if rate_limited(f"staff-code:{user.pk}", 8, 10 * 60):
            messages.error(request, "Too many codes tried. Wait 10 minutes.")
        else:
            otp = user.one_time_codes.filter(purpose=OneTimeCode.PURPOSE_STAFF_LOGIN, used_at__isnull=True).first()
            ok = bool(otp and otp.check_code(form.cleaned_data["code"].strip()))
            if otp:
                otp.used_at = timezone.now() if ok else None
                otp.save(update_fields=["attempts", "used_at"])  # a wrong try counts; five spend the code
            if ok:
                clear_rate_limit(f"staff-code:{user.pk}")
                _start_staff_session(request, user, two_factor_passed=True)
                return redirect(next_url)
            messages.error(request, "That code is not right, or it has run out. You can ask for a new one below.")
    elif request.method == "GET":
        issue_staff_code(user)  # one email per minute at most, so a page refresh does not spam the inbox

    local, _at, domain = user.email.partition("@")
    return render(request, "staff/two_factor.html", {
        "form": form, "email_hint": f"{local[:1]}***@{domain}", "minutes": STAFF_CODE_MINUTES,
    })


@require_POST
def staff_logout(request):
    logout(request)
    messages.info(request, "Signed out of the staff area.")
    return redirect("staff:login")
