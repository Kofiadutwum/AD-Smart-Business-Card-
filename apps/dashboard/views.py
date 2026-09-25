"""The subscriber's dashboard (Section 10)."""

import csv
from collections import Counter, OrderedDict
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import logout
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.forms import inlineformset_factory
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.decorators import customer_required
from apps.accounts.forms import AccountForm
from apps.billing.services import activate_cards, plan_for_limits
from apps.cards import services as card_services
from apps.cards.colors import palette
from apps.cards.models import ACCENT_PRESETS, Card, CardEvent, CardPhone, Lead, SocialLink
from apps.core.emails import notify_admins, send_email
from apps.core.models import AuditLog
from apps.nfc.models import NFCOrder

from .forms import CardForm, NewCardForm, PhoneForm, SlugForm, SocialLinkForm


def _cards(user):
    return user.cards.filter(deleted_at__isnull=True).order_by("created_at")


def _card(request, pk=None):
    cards = _cards(request.user)
    if pk is None:
        card = cards.first()
        if card is None:
            card = card_services.create_card(request.user, request.user.display_name or request.user.email.split("@")[0], email=request.user.email)
        return card
    card = cards.filter(pk=pk).first()
    if card is None:
        raise Http404
    return card


def _base(request, active, **extra):
    user = request.user
    subscription = user.subscription
    context = {
        "active": active,
        "subscription": subscription,
        "plan": plan_for_limits(user),
        "cards": list(_cards(user)),
    }
    context.update(extra)
    return context


# --------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------


@customer_required
def home(request):
    user = request.user
    cards = list(_cards(user))
    if not cards:
        return redirect("dashboard:editor")
    card = cards[0]
    since = timezone.now() - timedelta(days=30)
    events = CardEvent.objects.filter(card__in=cards, occurred_at__gte=since)
    counts = Counter(events.values_list("kind", flat=True))
    series = _daily(events.filter(kind=CardEvent.VIEW), 30)
    return render(
        request,
        "dashboard/home.html",
        _base(
            request,
            "home",
            card=card,
            views_30=counts.get(CardEvent.VIEW, 0),
            saves_30=counts.get(CardEvent.SAVE_CONTACT, 0),
            clicks_30=sum(v for k, v in counts.items() if k not in (CardEvent.VIEW, CardEvent.SAVE_CONTACT)),
            series=series,
            series_max=max(series.values()) if series else 0,
            unread_leads=Lead.objects.filter(card__in=cards, is_read=False).count(),
            open_orders=user.nfc_orders.exclude(status__in=[NFCOrder.DELIVERED, NFCOrder.CANCELLED, NFCOrder.REFUNDED]),
            qr_svg=card_services.qr_svg(card.public_url("qr"), scale=4, css_class="qr-mini"),
        ),
    )


def _daily(queryset, days):
    start = timezone.localdate() - timedelta(days=days - 1)
    buckets = OrderedDict((start + timedelta(days=i), 0) for i in range(days))
    for when in queryset.values_list("occurred_at", flat=True):
        day = timezone.localtime(when).date()
        if day in buckets:
            buckets[day] += 1
    return buckets


# --------------------------------------------------------------------------
# Card editor with live preview (Section 25)
# --------------------------------------------------------------------------


@customer_required
def editor(request, pk=None):
    card = _card(request, pk)
    plan = plan_for_limits(request.user)
    Phones = inlineformset_factory(
        Card, CardPhone, form=PhoneForm, extra=1 if card.phones.count() < plan.max_phones else 0,
        max_num=plan.max_phones, validate_max=True, can_delete=True,
    )
    form = CardForm(request.POST or None, request.FILES or None, instance=card, plan=plan)
    phones = Phones(request.POST or None, instance=card, prefix="phones")
    if request.method == "POST":
        if form.is_valid() and phones.is_valid():
            form.save()
            phones.save()
            for position, phone in enumerate(card.phones.all()):
                if phone.position != position:
                    CardPhone.objects.filter(pk=phone.pk).update(position=position)
            messages.success(request, "Card saved." + ("" if card.activated_at else " Preview it, then choose a plan to make it live."))
            return redirect(request.path)
        messages.error(request, "Some details need fixing. Look for the messages in red.")
    eff = card.effective()
    return render(
        request,
        "dashboard/editor.html",
        _base(
            request,
            "editor",
            card=card,
            form=form,
            phones=phones,
            slug_form=SlugForm(initial={"slug": card.slug}),
            preview={
                "card": card,
                "eff": eff,
                "pal": palette(eff["accent"], eff["card_colour"]),
                "card_url": card.public_url(),
                "vcf_url": "#",
                "demo": True,
            },
            presets=ACCENT_PRESETS,
            is_draft=card.activated_at is None,
        ),
    )


@customer_required
@require_POST
def change_slug(request, pk):
    card = _card(request, pk)
    form = SlugForm(request.POST)
    if form.is_valid():
        try:
            old = card.slug
            card_services.change_slug(card, form.cleaned_data["slug"])
            AuditLog.record(request.user, "slug_changed", card, old=old, new=card.slug)
            messages.success(request, f"Your link is now /c/{card.slug}. The old link redirects here.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    else:
        messages.error(request, "Enter a link between 3 and 40 characters.")
    return redirect("dashboard:editor_card", pk=card.pk)


# --------------------------------------------------------------------------
# Social links
# --------------------------------------------------------------------------


@customer_required
def links(request, pk=None):
    card = _card(request, pk)
    plan = plan_for_limits(request.user)
    form = SocialLinkForm(request.POST or None)
    count = card.social_links.count()
    limit = plan.max_social_links
    if request.method == "POST" and form.is_valid():
        if limit is not None and count >= limit:
            messages.warning(request, f"The {plan.name} plan includes {limit} social links. Upgrade for unlimited links.")
        else:
            link = form.save(commit=False)
            link.card = card
            link.position = count
            link.save()
            messages.success(request, f"{link.label} added.")
        return redirect(request.path)
    return render(
        request,
        "dashboard/links.html",
        _base(request, "links", card=card, form=form, links=card.social_links.all(), limit=limit, used=count),
    )


@customer_required
@require_POST
def link_action(request, link_id, action):
    link = get_object_or_404(SocialLink, pk=link_id, card__owner=request.user)
    card = link.card
    if action == "delete":
        link.delete()
        messages.info(request, "Link removed.")
    elif action in ("up", "down"):
        ordered = list(card.social_links.all())
        index = ordered.index(link)
        swap = index - 1 if action == "up" else index + 1
        if 0 <= swap < len(ordered):
            ordered[index], ordered[swap] = ordered[swap], ordered[index]
            for position, item in enumerate(ordered):
                SocialLink.objects.filter(pk=item.pk).update(position=position)
    return redirect("dashboard:links_card", pk=card.pk)


# --------------------------------------------------------------------------
# Share
# --------------------------------------------------------------------------


@customer_required
def share(request, pk=None):
    card = _card(request, pk)
    return render(
        request,
        "dashboard/share.html",
        _base(
            request,
            "share",
            card=card,
            card_url=card.public_url(),
            link_url=card.public_url("link"),
            qr_svg=card_services.qr_svg(card.public_url("qr"), scale=8),
            is_live=card.is_live,
            state=card.public_state(),
        ),
    )


# --------------------------------------------------------------------------
# Analytics (Section 11, plan-aware)
# --------------------------------------------------------------------------

PERIODS = {"7": 7, "30": 30, "90": 90, "365": 365}


@customer_required
def analytics(request, pk=None):
    card = _card(request, pk)
    plan = plan_for_limits(request.user)
    period = request.GET.get("days", "30")
    days = PERIODS.get(period, 30)
    since = timezone.now() - timedelta(days=days)
    events = card.events.filter(occurred_at__gte=since)
    total_views = card.events.filter(kind=CardEvent.VIEW).count()
    context = _base(request, "analytics", card=card, total_views=total_views, days=days, period=str(days))
    if plan.full_analytics:
        by_kind = dict(events.values_list("kind").annotate(n=Count("id")))
        views = events.filter(kind=CardEvent.VIEW)
        by_source = dict(views.values_list("source").annotate(n=Count("id")))
        if days <= 90:
            series = _daily(views, days)
            labels = [d.strftime("%d %b") for d in series]
        else:
            series = OrderedDict()
            for when in views.values_list("occurred_at", flat=True):
                key = timezone.localtime(when).strftime("%b %Y")
                series[key] = series.get(key, 0) + 1
            labels = list(series.keys())
        socials = dict(
            events.filter(kind=CardEvent.SOCIAL).values_list("detail").annotate(n=Count("id")).order_by("-n")[:8]
        )
        context.update(
            {
                "full": True,
                "views": by_kind.get(CardEvent.VIEW, 0),
                "unique": views.exclude(visitor_hash="").values("visitor_hash").distinct().count(),
                "saves": by_kind.get(CardEvent.SAVE_CONTACT, 0),
                "sources": [
                    ("QR code scans", by_source.get("qr", 0), "qr-code"),
                    ("NFC taps", by_source.get("nfc", 0), "nfc"),
                    ("Direct link", by_source.get("link", 0), "link"),
                ],
                "clicks": [
                    (label, by_kind.get(kind, 0))
                    for kind, label in CardEvent.KINDS
                    if kind not in (CardEvent.VIEW,)
                ],
                "series": list(zip(labels, series.values())),
                "series_max": max(series.values()) if series else 0,
                "socials": socials,
            }
        )
    return render(request, "dashboard/analytics.html", context)


# --------------------------------------------------------------------------
# Leads
# --------------------------------------------------------------------------


@customer_required
def leads(request):
    cards = list(_cards(request.user))
    rows = Lead.objects.filter(card__in=cards).select_related("card")
    unread = list(rows.filter(is_read=False).values_list("pk", flat=True))
    response = render(request, "dashboard/leads.html", _base(request, "leads", leads=rows, unread=set(unread)))
    if unread:
        Lead.objects.filter(pk__in=unread).update(is_read=True)
    return response


@customer_required
def leads_csv(request):
    rows = Lead.objects.filter(card__owner=request.user).select_related("card")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="contacts.csv"'
    response.write("﻿")  # so Excel opens UTF-8 names correctly
    writer = csv.writer(response)
    writer.writerow(["Date", "Name", "Phone", "Email", "Company", "Message", "Came via", "Card"])
    for lead in rows:
        writer.writerow([
            timezone.localtime(lead.created_at).strftime("%Y-%m-%d %H:%M"), lead.name, lead.phone, lead.email,
            lead.organisation, (lead.note or "").replace("\n", " "), lead.source, lead.card.slug,
        ])
    return response


@customer_required
@require_POST
def lead_delete(request, pk):
    get_object_or_404(Lead, pk=pk, card__owner=request.user).delete()
    messages.info(request, "Contact removed.")
    return redirect("dashboard:leads")


# --------------------------------------------------------------------------
# Several cards (Business plan) and choosing which stay live (PLN-02)
# --------------------------------------------------------------------------


@customer_required
def cards_list(request):
    user = request.user
    plan = plan_for_limits(user)
    form = NewCardForm(request.POST or None)
    cards = list(_cards(user))
    enabled = [c for c in cards if c.is_enabled]
    if request.method == "POST" and form.is_valid():
        if len(cards) >= plan.max_cards:
            messages.warning(request, f"The {plan.name} plan includes {plan.max_cards} card{'s' if plan.max_cards != 1 else ''}.")
        else:
            card = card_services.create_card(user, form.cleaned_data["full_name"], job_title=form.cleaned_data["job_title"])
            messages.success(request, "New card created. Fill in the details.")
            return redirect("dashboard:editor_card", pk=card.pk)
    return render(
        request, "dashboard/cards.html",
        _base(request, "cards", form=form, enabled_count=len(enabled), over_limit=len(enabled) > plan.max_cards),
    )


@customer_required
@require_POST
def card_toggle(request, pk):
    card = _card(request, pk)
    plan = plan_for_limits(request.user)
    enabled = _cards(request.user).filter(is_enabled=True).count()
    if not card.is_enabled and enabled >= plan.max_cards:
        messages.warning(request, "Switch another card off first, or upgrade for more cards.")
    else:
        card.is_enabled = not card.is_enabled
        card.save(update_fields=["is_enabled"])
        messages.success(request, f"{card.full_name} is now {'live' if card.is_enabled else 'switched off'}.")
    return redirect("dashboard:cards")


@customer_required
@require_POST
def card_delete(request, pk):
    card = _card(request, pk)
    if _cards(request.user).count() <= 1:
        messages.error(request, "You need at least one card. Edit this one instead.")
        return redirect("dashboard:cards")
    card_services.retire_card(card)
    AuditLog.record(request.user, "card_deleted_by_owner", card)
    messages.info(request, f"{card.full_name}'s card was deleted. Its link will show that the card no longer exists.")
    return redirect("dashboard:cards")


# --------------------------------------------------------------------------
# NFC orders and account
# --------------------------------------------------------------------------


@customer_required
def nfc_orders(request):
    orders = request.user.nfc_orders.prefetch_related("items__card").order_by("-created_at")
    return render(request, "dashboard/nfc_orders.html", _base(request, "nfc", orders=orders))


@customer_required
def settings_view(request):
    form = AccountForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Account details saved.")
        return redirect("dashboard:settings")
    return render(request, "dashboard/settings.html", _base(request, "settings", form=form))


@customer_required
@require_POST
def request_deletion(request):
    """PRV-04: personal data is removed within 30 days; slugs are retired."""
    user = request.user
    if request.POST.get("confirm_email", "").strip().lower() != user.email.lower():
        messages.error(request, "Type your email address exactly to confirm.")
        return redirect("dashboard:settings")
    user.deletion_requested_at = timezone.now()
    user.save(update_fields=["deletion_requested_at"])
    for card in _cards(user):
        card.is_published = False
        card.save(update_fields=["is_published"])
    AuditLog.record(user, "deletion_requested", user)
    send_email(to=user.email, subject="We received your account deletion request", template="deletion_requested",
               context={"user": user}, user=user, kind="deletion_requested")
    notify_admins(f"Account deletion requested: {user.email}", "admin_deletion_requested", {"user_obj": user})
    logout(request)
    messages.info(request, "Your deletion request is recorded. Your data will be removed within 30 days.")
    return redirect("marketing:home")
