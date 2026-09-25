"""Subscribers: search, detail, manual activation, suspension (ADM-01, ADM-03, ADM-06, MOD-04)."""

from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.billing.services import manual_activation
from apps.cards.models import Card
from apps.cards.views import card_context
from apps.core.emails import send_email
from apps.core.models import AuditLog, SiteSettings
from apps.core.utils import ghs_to_minor, safe_next

from ..forms import ManualActivationForm, ReasonForm
from ..permissions import can, staff_required


def _customers():
    return User.objects.filter(is_staff=False).select_related("subscription_record__plan")


@staff_required("view_subscribers")
def subscribers(request):
    qs = _customers().order_by("-date_joined")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(email__icontains=q)
            | Q(full_name__icontains=q)
            | Q(phone__icontains=q.lstrip("0"))
            | Q(cards__full_name__icontains=q)
            | Q(cards__slug__icontains=q)
            | Q(cards__slug_history__slug__icontains=q)
            | Q(payments__reference__iexact=q)
        ).distinct()
    state = request.GET.get("state", "")
    now = timezone.now()
    grace = timedelta(days=SiteSettings.load().grace_period_days)
    if state == "active":
        qs = qs.filter(subscription_record__expires_at__gte=now)
    elif state == "expiring":
        qs = qs.filter(subscription_record__expires_at__gte=now, subscription_record__expires_at__lte=now + timedelta(days=30))
    elif state == "grace":
        qs = qs.filter(subscription_record__expires_at__lt=now, subscription_record__expires_at__gte=now - grace)
    elif state == "inactive":
        qs = qs.filter(subscription_record__expires_at__lt=now - grace)
    elif state == "draft":
        qs = qs.filter(subscription_record__isnull=True)
    elif state == "suspended":
        qs = qs.filter(is_suspended=True)
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "staff/subscribers.html", {"page": page, "q": q, "state": state})


@staff_required("view_subscribers")
def subscriber(request, pk):
    customer = get_object_or_404(_customers(), pk=pk)
    context = {
        "customer": customer,
        "subscription": customer.subscription,
        "cards": customer.cards.all().prefetch_related("phones"),
        "orders": customer.nfc_orders.all()[:20],
        "tickets": customer.support_requests.all()[:10],
        "activation_form": ManualActivationForm(),
        "reason_form": ReasonForm(),
        "emails": customer.emaillog_set.all()[:15],
    }
    if can(request.user, "view_payments"):
        context["payments"] = customer.payments.select_related("plan")[:30]
    return render(request, "staff/subscriber.html", context)


@staff_required("view_subscribers")
def card_preview(request, pk):
    """ADM-06: see the card exactly as the public does, whatever its status."""
    card = get_object_or_404(Card, pk=pk)
    context = card_context(card, request)
    context.update({"demo": True, "staff_preview": True, "state": card.public_state()})
    return render(request, "staff/card_preview.html", context)


@staff_required("manual_activation")
@require_POST
def activate(request, pk):
    customer = get_object_or_404(_customers(), pk=pk)
    form = ManualActivationForm(request.POST)
    if form.is_valid():
        data = form.cleaned_data
        payment = manual_activation(
            staff=request.user, user=customer, plan=data["plan"], months=data["months"],
            reason=data["reason"], amount_minor=ghs_to_minor(data["amount"]),
        )
        messages.success(request, f"Subscription extended ({payment.reference}). A receipt was emailed to the customer.")
    else:
        messages.error(request, "Choose a plan, the number of months and give a reason.")
    return redirect("staff:subscriber", pk=pk)


@staff_required("suspend")
@require_POST
def suspend(request, pk):
    customer = get_object_or_404(_customers(), pk=pk)
    form = ReasonForm(request.POST)
    if not form.is_valid():
        messages.error(request, "A reason is required.")
        return redirect("staff:subscriber", pk=pk)
    reason = form.cleaned_data["reason"]
    customer.is_suspended = not customer.is_suspended
    customer.suspension_reason = reason if customer.is_suspended else ""
    customer.save(update_fields=["is_suspended", "suspension_reason"])
    action = "account_suspended" if customer.is_suspended else "account_reinstated"
    AuditLog.record(request.user, action, customer, reason=reason)
    send_email(
        to=customer.email,
        subject="Your AD Smart account has been suspended" if customer.is_suspended else "Your AD Smart account is active again",
        template="account_suspended" if customer.is_suspended else "account_reinstated",
        context={"user": customer, "reason": reason}, user=customer, kind=action,
    )
    messages.success(request, f"Account {'suspended' if customer.is_suspended else 'reinstated'}. The customer was emailed.")
    return redirect("staff:subscriber", pk=pk)


@staff_required("suspend")
@require_POST
def suspend_card(request, pk):
    card = get_object_or_404(Card, pk=pk)
    form = ReasonForm(request.POST)
    if not form.is_valid():
        messages.error(request, "A reason is required.")
        return redirect("staff:subscriber", pk=card.owner_id)
    reason = form.cleaned_data["reason"]
    card.is_suspended = not card.is_suspended
    card.suspension_reason = reason if card.is_suspended else ""
    card.save(update_fields=["is_suspended", "suspension_reason"])
    action = "card_suspended" if card.is_suspended else "card_reinstated"
    AuditLog.record(request.user, action, card, reason=reason)
    send_email(
        to=card.owner.email,
        subject=f"Your card /c/{card.slug} has been {'suspended' if card.is_suspended else 'reinstated'}",
        template="card_suspended" if card.is_suspended else "card_reinstated",
        context={"user": card.owner, "card": card, "reason": reason}, user=card.owner, kind=action,
    )
    messages.success(request, f"Card {'suspended' if card.is_suspended else 'reinstated'}.")
    next_url = safe_next(request.POST.get("next"))
    return redirect(next_url) if next_url else redirect("staff:subscriber", pk=card.owner_id)


@staff_required("manage_admins")
@require_POST
def reset_2fa(request, pk):
    member = get_object_or_404(User, pk=pk, is_staff=True)
    member.totp_secret = ""
    member.totp_confirmed_at = None
    member.save(update_fields=["totp_secret", "totp_confirmed_at"])
    AuditLog.record(request.user, "staff_2fa_reset", member)
    messages.success(request, f"{member.email} will set up two-factor again at their next sign-in.")
    return redirect("staff:admins")
