"""Registration, email verification, sign-in, recovery and Google sign-in (Figures 2A, 3A, 3B)."""

import secrets

from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.cards.services import create_card
from apps.core.emails import send_email
from apps.core.utils import clear_rate_limit, client_ip, rate_limited, safe_next

from . import services
from .forms import LoginForm, PasswordChangeForm, RecoveryRequestForm, RecoveryVerifyForm, RegisterForm
from .models import OneTimeCode, User

BACKEND = "django.contrib.auth.backends.ModelBackend"


def _after_login(request, user):
    target = safe_next(request.POST.get("next") or request.GET.get("next"))
    if user.is_staff:
        return redirect("staff:two_factor")
    return redirect(target or "dashboard:home")


# --------------------------------------------------------------------------
# Registration and verification (REG-01, REG-02, REG-09)
# --------------------------------------------------------------------------


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")
    if request.GET.get("plan"):
        request.session["intended_plan"] = request.GET["plan"][:32]
    form = RegisterForm(request.POST or None)
    if request.method == "POST":
        if rate_limited(f"register:{client_ip(request)}", 10, 3600):
            messages.error(request, "Too many sign-ups from this connection. Try again in an hour.")
        elif form.is_valid():
            data = form.cleaned_data
            with transaction.atomic():
                user = User.objects.create_user(
                    email=data["email"],
                    password=data["password"],
                    full_name=data["full_name"].strip(),
                    phone=data["phone"],
                    accepted_terms_at=timezone.now(),
                )
                create_card(user, data["full_name"].strip(), email=data["email"])
            login(request, user, backend=BACKEND)
            services.send_verification_email(user)
            send_email(
                to=user.email, subject="Welcome to AD Smart Business Cards", template="welcome",
                context={"user": user}, user=user, kind="welcome", dedupe_key=f"welcome:{user.pk}",
            )
            return redirect("accounts:verify_pending")
    return render(request, "accounts/register.html", {"form": form, "google_enabled": services.google_configured()})


@login_required
def verify_pending(request):
    if request.user.is_email_verified:
        return redirect("dashboard:home")
    if request.method == "POST":
        if rate_limited(f"verify-resend:{request.user.pk}", 5, 3600):
            messages.error(request, "You have asked for several emails already. Check your spam folder, or try again later.")
        else:
            services.send_verification_email(request.user)
            messages.success(request, f"We sent a new link to {request.user.email}.")
        return redirect("accounts:verify_pending")
    return render(request, "accounts/verify_pending.html")


def verify_email(request, token):
    data = services.read_verification_token(token)
    user = User.objects.filter(pk=(data or {}).get("uid")).first() if data else None
    if user is None or user.email != data.get("email"):
        messages.error(request, "That link has expired or was already used. Sign in and ask for a new one.")
        return redirect("accounts:login")
    if not user.email_verified_at:
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at"])
    if not request.user.is_authenticated:
        login(request, user, backend=BACKEND)
    messages.success(request, "Email confirmed. Build your card, preview it, then choose a plan.")
    return redirect("dashboard:editor")


# --------------------------------------------------------------------------
# Sign in and out (REG-03, SEC-04)
# --------------------------------------------------------------------------


def login_view(request):
    if request.user.is_authenticated:
        return redirect("staff:home" if request.user.is_staff else "dashboard:home")
    form = LoginForm(request.POST or None)
    status = 200
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        key = f"login:{client_ip(request)}:{email}"
        if rate_limited(key, 5, 15 * 60):
            messages.error(request, "Too many attempts. Wait 15 minutes, or reset your password.")
            status = 429
        else:
            user = User.objects.filter(email__iexact=email).first()
            if user is None or not user.check_password(form.cleaned_data["password"]) or not user.is_active:
                messages.error(request, "That email and password do not match.")
                status = 401
            elif user.is_suspended:
                messages.error(request, "This account is suspended. Contact support.")
                status = 403
            else:
                clear_rate_limit(key)
                login(request, user, backend=BACKEND)
                if not form.cleaned_data.get("remember"):
                    request.session.set_expiry(0)
                return _after_login(request, user)
    return render(
        request,
        "accounts/login.html",
        {"form": form, "google_enabled": services.google_configured(), "next": request.GET.get("next", "")},
        status=status,
    )


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, "You are signed out.")
    return redirect("marketing:home")


# --------------------------------------------------------------------------
# Password recovery by emailed code (REG-05..08, Figure 3B)
# --------------------------------------------------------------------------

GENERIC = "If an account uses that email, we have sent it a 6-digit code. It expires in 15 minutes."


def forgot_password(request):
    form = RecoveryRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        if rate_limited(f"reset-request:{client_ip(request)}", 5, 3600):
            messages.error(request, "Too many requests. Try again in an hour.")
            return redirect("accounts:forgot_password")
        user = User.objects.filter(email__iexact=email, is_active=True, is_suspended=False).first()
        if user and not services.issue_reset_code(user):
            messages.warning(request, "A code was sent less than a minute ago. Check your inbox before asking again.")
        request.session["reset_email"] = email
        messages.info(request, GENERIC)
        return redirect("accounts:reset_password")
    return render(request, "accounts/forgot_password.html", {"form": form})


def reset_password(request):
    email = request.session.get("reset_email")
    if not email:
        return redirect("accounts:forgot_password")
    form = RecoveryVerifyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if rate_limited(f"reset-verify:{client_ip(request)}", 20, 3600):
            messages.error(request, "Too many attempts from this connection. Try again later.")
            return redirect("accounts:reset_password")
        user = User.objects.filter(email__iexact=email).first()
        otp = (
            user.one_time_codes.filter(purpose=OneTimeCode.PURPOSE_PASSWORD_RESET).first() if user else None
        )
        if otp is None or not otp.is_usable:
            messages.error(request, "That code has expired or was used too many times. Ask for a new one.")
        elif otp.check_code(form.cleaned_data["code"].strip()):
            otp.used_at = timezone.now()
            otp.save()
            user.set_password(form.cleaned_data["password"])
            user.save(update_fields=["password"])
            request.session.pop("reset_email", None)
            send_email(
                to=user.email, subject="Your password was changed", template="password_changed",
                context={"user": user}, user=user, kind="password_changed",
            )
            login(request, user, backend=BACKEND)
            messages.success(request, "Your password has been reset.")
            return _after_login(request, user)
        else:
            otp.save()
            left = OneTimeCode.MAX_ATTEMPTS - otp.attempts
            if left > 0:
                messages.error(request, f"That code is not right. {left} attempt{'s' if left != 1 else ''} left.")
            else:
                messages.error(request, "Too many wrong codes. Ask for a new one.")
    return render(request, "accounts/reset_password.html", {"form": form, "email": email})


@login_required
def change_password(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        request.user.set_password(form.cleaned_data["password"])
        request.user.save(update_fields=["password"])
        update_session_auth_hash(request, request.user)
        send_email(
            to=request.user.email, subject="Your password was changed", template="password_changed",
            context={"user": request.user}, user=request.user, kind="password_changed",
        )
        messages.success(request, "Password updated.")
        return redirect("dashboard:settings")
    return render(request, "accounts/change_password.html", {"form": form})


# --------------------------------------------------------------------------
# Google
# --------------------------------------------------------------------------


def google_start(request):
    if not services.google_configured():
        messages.warning(request, "Google sign-in is not available yet.")
        return redirect("accounts:login")
    state = secrets.token_urlsafe(32)
    request.session["google_state"] = state
    request.session["google_next"] = safe_next(request.GET.get("next")) or ""
    return redirect(services.google_authorization_url(state))


def google_callback(request):
    expected = request.session.pop("google_state", None)
    target = request.session.pop("google_next", "") or None
    if request.GET.get("error"):
        messages.info(request, "Google sign-in was cancelled.")
        return redirect("accounts:login")
    received = request.GET.get("state", "")
    if not expected or not secrets.compare_digest(received, expected):
        messages.error(request, "Your Google sign-in session expired. Try again.")
        return redirect("accounts:login")
    profile, error = services.google_user_from_code(request.GET.get("code", ""))
    if error:
        messages.error(request, error)
        return redirect("accounts:login")

    user = User.objects.filter(google_sub=profile["sub"]).first()
    if user is None:
        user = User.objects.filter(email__iexact=profile["email"]).first()
        if user is not None:
            user.google_sub = profile["sub"]
            user.email_verified_at = user.email_verified_at or timezone.now()
            user.save(update_fields=["google_sub", "email_verified_at"])
    if user is None:
        with transaction.atomic():
            user = User.objects.create_user(
                email=profile["email"],
                password=None,
                full_name=profile["name"],
                google_sub=profile["sub"],
                email_verified_at=timezone.now(),
                accepted_terms_at=timezone.now(),
            )
            create_card(user, profile["name"], email=profile["email"])
        send_email(
            to=user.email, subject="Welcome to AD Smart Business Cards", template="welcome",
            context={"user": user}, user=user, kind="welcome", dedupe_key=f"welcome:{user.pk}",
        )
    if user.is_suspended or not user.is_active:
        messages.error(request, "This account is suspended. Contact support.")
        return redirect("accounts:login")
    login(request, user, backend=BACKEND)
    if user.is_staff:
        return redirect("staff:two_factor")
    if not user.phone:
        messages.info(request, "Add your mobile number so we can reach you about orders and delivery.")
        return redirect("dashboard:settings")
    return redirect(target or reverse("dashboard:home"))
