"""Setting up a pre-made card: the page a buyer reaches by tapping or scanning it."""

import time

from django.contrib import messages
from django.contrib.auth import login
from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.accounts import services as account_services
from apps.accounts.views import BACKEND
from apps.core.emails import send_email, subject_text
from apps.core.utils import client_ip, rate_limited

from . import premade as premade_service
from .forms import ClaimForm, PinForm
from .models import Card
from .premade import CLAIM_WINDOW_SECONDS, ClaimError
from django.utils.translation import gettext as _


def _premade_for(request, slug):
    """The waiting card for this link, or a response to send instead."""
    card = Card.objects.select_related("premade__batch__plan").filter(slug=(slug or "").lower()).first()
    premade = getattr(card, "premade", None) if card else None
    if premade is None:
        return None, render(request, "cards/state.html", {"state": "missing"}, status=404)
    if card.deleted_at or premade.voided_at:
        return None, render(request, "cards/state.html", {"state": "deleted", "card": card}, status=410)
    if premade.claimed_at or card.owner_id:
        return None, redirect(card.get_absolute_url())  # already set up: show the card itself
    return premade, None


def _verified(request, slug):
    stamp = (request.session.get("premade_ok") or {}).get(slug)
    return bool(stamp) and time.time() - stamp < CLAIM_WINDOW_SECONDS


def _mark_verified(request, slug):
    ok = request.session.get("premade_ok") or {}
    ok[slug] = time.time()
    request.session["premade_ok"] = ok


def _forget(request, slug):
    ok = request.session.get("premade_ok") or {}
    ok.pop(slug, None)
    request.session["premade_ok"] = ok


def claim(request, slug):
    """Step 1: the PIN on the back proves the buyer holds the card."""
    premade, response = _premade_for(request, slug)
    if response:
        return response
    form = PinForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            premade_service.check_pin(premade, form.cleaned_data["pin"], request)
        except ClaimError as exc:
            form.add_error("pin", str(exc))
        else:
            _mark_verified(request, premade.card.slug)
            target = "cards:claim_attach" if request.user.is_authenticated else "cards:claim_setup"
            return redirect(target, slug=premade.card.slug)
    return render(request, "cards/claim_pin.html", {"form": form, "card": premade.card, "batch": premade.batch})


def claim_setup(request, slug):
    """Step 2: the buyer's details and a new account. The year of the plan starts now."""
    premade, response = _premade_for(request, slug)
    if response:
        return response
    if not _verified(request, premade.card.slug):
        return redirect("cards:claim", slug=premade.card.slug)
    if request.user.is_authenticated:
        return redirect("cards:claim_attach", slug=premade.card.slug)
    form = ClaimForm(request.POST or None)
    if request.method == "POST":
        if rate_limited(f"claim:{client_ip(request)}", 10, 3600):
            messages.error(request, _("Too many attempts from this connection. Try again in an hour."))
        elif form.is_valid():
            try:
                user, card, _payment = premade_service.claim_with_new_account(premade.pk, form.cleaned_data)
            except ClaimError as exc:
                messages.error(request, str(exc))
                return redirect("cards:public", slug=premade.card.slug)
            except IntegrityError:
                messages.error(request, _("That email or number was just used for another account. Please check and try again."))
            else:
                login(request, user, backend=BACKEND)
                _forget(request, card.slug)
                account_services.send_verification_email(user)
                send_email(
                    to=user.email, subject=subject_text("Welcome to AD Smart Business Cards"), template="welcome",
                    context={"user": user}, user=user, kind="welcome", dedupe_key=f"welcome:{user.pk}",
                )
                messages.success(
                    request,
                    _("Your card is live and your first year is active. We sent a link to %(email)s so you can confirm your address when you are ready.")
                    % {"email": user.email},
                )
                return redirect("dashboard:home")
    return render(
        request, "cards/claim_setup.html",
        {"form": form, "card": premade.card, "batch": premade.batch,
         "login_url": f"{reverse('accounts:login')}?next={reverse('cards:claim_attach', args=[premade.card.slug])}"},
    )


def claim_attach(request, slug):
    """For a buyer who already has an account: add the card to it and add the year."""
    premade, response = _premade_for(request, slug)
    if response:
        return response
    if not _verified(request, premade.card.slug):
        return redirect("cards:claim", slug=premade.card.slug)
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={reverse('cards:claim_attach', args=[premade.card.slug])}")
    problem = premade_service.attach_problem(request.user, premade)
    if request.method == "POST" and not problem:
        try:
            card, _payment = premade_service.claim_for_existing_account(premade.pk, request.user)
        except ClaimError as exc:
            messages.error(request, str(exc))
        else:
            _forget(request, card.slug)
            messages.success(request, _("The card is now on your account and a year has been added to your plan."))
            return redirect("dashboard:cards")
    return render(
        request, "cards/claim_attach.html",
        {"card": premade.card, "batch": premade.batch, "problem": problem, "subscription": request.user.subscription},
    )
