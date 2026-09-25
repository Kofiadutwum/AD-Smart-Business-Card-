"""Quotes, checkout, verification and fulfilment.

Order of trust, stated once (Figures 4A-4C):

    1. A Payment row is written as 'pending' BEFORE Paystack is called, so
       nothing can succeed at the gateway without a local record.
    2. The browser's return (callback) and Paystack's webhook both lead to
       ``verify_and_fulfil``, which asks Paystack for the truth and checks
       reference, amount and currency against the local record.
    3. ``fulfil`` locks the payment row and only acts on a pending payment,
       so a replayed webhook or a refreshed callback can never grant a
       second year or confirm an order twice (PAY-07, AC-08).
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from apps.core.emails import notify_admins, send_email
from apps.core.models import AuditLog, Counter, SiteSettings
from apps.core.utils import percent_of

from . import fx, paystack
from .models import Payment, Plan, PromoCode, Subscription

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Plans
# --------------------------------------------------------------------------


def active_plans():
    return Plan.objects.filter(is_active=True).order_by("sort_order", "price_minor")


def plan_for_limits(user):
    """The plan whose allowances apply to this user's cards.

    Subscribers get their own plan, even after it lapses, so a downgrade or
    an expiry can never unlock features. Draft users preview with the top
    plan, and the editor marks which features need which plan.
    """
    subscription = getattr(user, "subscription", None)
    if subscription is not None:
        return subscription.plan
    return Plan.objects.order_by("-price_minor").first() or Plan(
        code="preview", name="Preview", price_minor=0, max_cards=1, max_phones=3,
        all_templates=True, custom_colours=True, allow_logo=True, allow_advanced_style=True,
        full_analytics=True,
    )


# --------------------------------------------------------------------------
# Quotes
# --------------------------------------------------------------------------


def tax_on(amount_minor):
    rate = SiteSettings.load().tax_rate_percent
    return percent_of(amount_minor, rate) if rate else 0


def resolve_promo(code, scope):
    """Return (promo, error). Blank codes are simply (None, None)."""
    code = (code or "").strip().upper()
    if not code:
        return None, None
    promo = PromoCode.objects.filter(code=code).first()
    if promo is None:
        return None, "We could not find that promo code."
    problem = promo.problem_for(scope)
    if problem:
        return None, problem
    return promo, None


def build_quote(lines, promo=None):
    """lines: list of (label, amount_minor). Returns a quote dict."""
    subtotal = sum(amount for _, amount in lines)
    discount = promo.discount_on(subtotal) if promo else 0
    taxable = subtotal - discount
    tax = tax_on(taxable)
    return {
        "lines": [{"label": label, "amount_minor": amount} for label, amount in lines],
        "subtotal_minor": subtotal,
        "discount_minor": discount,
        "tax_minor": tax,
        "total_minor": taxable + tax,
        "promo": promo,
        "tax_label": SiteSettings.load().tax_label,
    }


def upgrade_price(subscription, new_plan):
    """PLN-01: the price difference, pro-rated by full months remaining."""
    months = subscription.full_months_left
    difference = max(0, new_plan.price_minor - subscription.plan.price_minor)
    return (difference * months) // subscription.plan.duration_months, months


def subscription_quote(user, plan, promo=None):
    """New subscription, renewal, or a renewal onto a different plan."""
    subscription = user.subscription
    label = f"{plan.name} plan, 12 months"
    if subscription and subscription.expires_at > timezone.now():
        label = f"{plan.name} plan, 12 more months"
    return build_quote([(label, plan.price_minor)], promo)


# --------------------------------------------------------------------------
# Checkout
# --------------------------------------------------------------------------


def create_payment(*, user, purpose, quote, plan=None, months=0, nfc_order=None):
    rate = fx.latest_rate()
    return Payment.objects.create(
        user=user,
        reference=paystack.new_reference("NFC" if purpose == Payment.PURPOSE_NFC else "ADS"),
        purpose=purpose,
        plan=plan,
        months=months,
        nfc_order=nfc_order,
        subtotal_minor=quote["subtotal_minor"],
        discount_minor=quote["discount_minor"],
        tax_minor=quote["tax_minor"],
        amount_minor=quote["total_minor"],
        currency=settings.CURRENCY,
        promo_code=quote.get("promo"),
        line_items=quote["lines"],
        fx_rate=rate.ghs_per_usd if rate else None,
        fx_rate_at=rate.fetched_at if rate else None,
    )


def checkout_url(payment, request):
    """Send the customer to Paystack's hosted page (Figure 4A).

    A zero-amount payment (a 100% promo code) is fulfilled straight away.
    """
    if payment.amount_minor == 0:
        fulfil(payment, {"status": "success", "channel": "promo", "fees": 0})
        return reverse("billing:result", args=[payment.reference])
    callback = settings.SITE_URL + (
        reverse("nfc:callback") if payment.purpose == Payment.PURPOSE_NFC else reverse("billing:callback")
    )
    data = paystack.initialize(
        email=payment.user.email,
        amount_minor=payment.amount_minor,
        reference=payment.reference,
        callback_url=callback,
        metadata={
            "payment_id": payment.pk,
            "purpose": payment.purpose,
            "custom_fields": [
                {"display_name": "Item", "variable_name": "item", "value": payment.description}
            ],
        },
    )
    return data["authorization_url"]


# --------------------------------------------------------------------------
# Verification and fulfilment
# --------------------------------------------------------------------------


def gateway_matches(payment, data):
    """Reference, amount and currency must all match the local record."""
    problems = []
    if str(data.get("reference") or payment.reference) != payment.reference:
        problems.append("reference")
    try:
        if int(data.get("amount")) != payment.amount_minor:
            problems.append("amount")
    except (TypeError, ValueError):
        problems.append("amount")
    if str(data.get("currency") or "").upper() != payment.currency.upper():
        problems.append("currency")
    return problems


def verify_and_fulfil(payment, source):
    """Confirm with Paystack and fulfil. Returns the payment's final status."""
    if payment.status != Payment.PENDING:
        return payment.status
    try:
        data = paystack.verify(
            payment.reference, expected_amount=payment.amount_minor, expected_currency=payment.currency
        )
    except paystack.PaymentError:
        return payment.status  # stay pending; the webhook or the daily job will retry

    status = data.get("status")
    if status == "success":
        problems = gateway_matches(payment, data)
        if problems:
            flag_payment(payment, "mismatch:" + ",".join(problems), data)
            return payment.status
        fulfil(payment, data, source=source)
    elif status in {"failed", "abandoned", "reversed"}:
        Payment.objects.filter(pk=payment.pk, status=Payment.PENDING).update(
            status=Payment.FAILED if status != "abandoned" else Payment.ABANDONED,
            gateway_response=_trim(data),
        )
    payment.refresh_from_db()
    return payment.status


def flag_payment(payment, flag, data):
    Payment.objects.filter(pk=payment.pk).update(flag=flag[:64], gateway_response=_trim(data))
    notify_admins(
        f"Payment needs checking: {payment.reference}",
        "admin_payment_flag",
        {"payment": payment, "flag": flag},
        dedupe_key=f"flag:{payment.reference}",
    )
    logger.warning("Payment %s flagged: %s", payment.reference, flag)


def _trim(data):
    keep = ("status", "reference", "amount", "currency", "channel", "fees", "paid_at", "gateway_response", "id")
    return {k: data.get(k) for k in keep if k in data}


def fulfil(payment, data, source="callback", actor=None):
    """Turn a verified payment into subscription time or a confirmed order."""
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.status != Payment.PENDING:
            return False
        locked.status = Payment.SUCCESS
        locked.paid_at = timezone.now()
        locked.channel = str(data.get("channel") or locked.channel or "")[:32]
        locked.gateway_fee_minor = int(data.get("fees") or 0)
        locked.gateway_response = _trim(data)
        locked.receipt_number = f"ADR-{timezone.localdate():%Y}-{Counter.next(f'receipt-{timezone.localdate():%Y}'):05d}"
        locked.save()

        if locked.promo_code_id:
            PromoCode.objects.filter(pk=locked.promo_code_id).update(used_count=F("used_count") + 1)

        if locked.purpose in (Payment.PURPOSE_SUBSCRIPTION, Payment.PURPOSE_UPGRADE):
            apply_subscription_payment(locked)
        elif locked.purpose == Payment.PURPOSE_NFC:
            if locked.plan_id:
                activate(locked.user, locked.plan, locked.months or locked.plan.duration_months)
            from apps.nfc.services import mark_paid

            mark_paid(locked.nfc_order, locked)

        AuditLog.record(
            actor, "payment_verified", locked, reason=f"Verified via {source}",
            amount_minor=locked.amount_minor, reference=locked.reference,
        )
    transaction.on_commit(lambda: send_receipt(locked))
    payment.refresh_from_db()
    return True


def apply_subscription_payment(payment):
    user, plan = payment.user, payment.plan
    subscription = getattr(user, "subscription", None)
    if payment.purpose == Payment.PURPOSE_UPGRADE and subscription:
        subscription.plan = plan
        subscription.pending_plan = None
        subscription.pending_plan_from = None
        subscription.save()
        return subscription
    return activate(user, plan, payment.months or plan.duration_months)


def activate(user, plan, months):
    """Start or extend the account's subscription and bring its cards live.

    Renewing onto a cheaper plan while time remains is a downgrade: it waits
    for the current period to end (PLN-02). Renewing onto a dearer plan
    upgrades at once.
    """
    now = timezone.now()
    subscription = Subscription.objects.select_for_update().filter(user=user).first()
    if subscription is None:
        subscription = Subscription(user=user, plan=plan, started_at=now, expires_at=now)
        subscription.extend(months)
    else:
        still_running = subscription.expires_at > now and not subscription.cancelled_at
        if still_running and plan.price_minor < subscription.plan.price_minor:
            subscription.pending_plan = plan
            subscription.pending_plan_from = subscription.expires_at
        else:
            subscription.plan = plan
            subscription.pending_plan = None
            subscription.pending_plan_from = None
        subscription.extend(months)
    subscription.save()
    activate_cards(user, subscription)
    return subscription


def activate_cards(user, subscription=None):
    """Draft cards go live on first activation; plan limits pick which (PLN-02)."""
    subscription = subscription or user.subscription
    if subscription is None:
        return
    limit = subscription.plan.max_cards
    cards = list(user.cards.filter(deleted_at__isnull=True).order_by("created_at"))
    # Keep the owner's own choice while it fits the plan; otherwise the oldest
    # cards stay enabled and the owner can swap them in the dashboard.
    keep = {c.pk for c in cards if c.is_enabled}
    if len(keep) > limit or not keep:
        keep = {c.pk for c in cards[:limit]}
    for card in cards:
        changed = []
        if card.activated_at is None:
            card.activated_at = timezone.now()
            changed.append("activated_at")
        enabled = card.pk in keep
        if card.is_enabled != enabled:
            card.is_enabled = enabled
            changed.append("is_enabled")
        if changed:
            card.save(update_fields=changed)


def manual_activation(*, staff, user, plan, months, reason, amount_minor=0):
    """ADM-01: cash payments, complimentary cards, failed webhooks."""
    with transaction.atomic():
        payment = Payment.objects.create(
            user=user,
            reference=paystack.new_reference("MAN"),
            purpose=Payment.PURPOSE_SUBSCRIPTION,
            plan=plan,
            months=months,
            subtotal_minor=amount_minor,
            amount_minor=amount_minor,
            gateway="manual",
            channel="cash" if amount_minor else "complimentary",
            manual_reason=reason,
            line_items=[{"label": f"{plan.name} plan, {months} months (manual)", "amount_minor": amount_minor}],
        )
        fulfil(payment, {"status": "success", "channel": payment.channel, "fees": 0}, source="manual", actor=staff)
        AuditLog.record(staff, "manual_activation", user, reason=reason, plan=plan.code, months=months)
    return payment


# --------------------------------------------------------------------------
# Receipts (PAY-08)
# --------------------------------------------------------------------------


def send_receipt(payment):
    payment.refresh_from_db()
    if payment.receipt_emailed_at or payment.status != Payment.SUCCESS:
        return
    from .receipts import render_receipt_pdf

    subscription = getattr(payment.user, "subscription", None)
    sent = send_email(
        to=payment.user.email,
        subject=f"Your receipt {payment.receipt_number}",
        template="payment_receipt",
        context={"payment": payment, "user": payment.user, "subscription": subscription},
        user=payment.user,
        kind="payment_receipt",
        dedupe_key=f"receipt:{payment.reference}",
        attachments=[(f"{payment.receipt_number}.pdf", render_receipt_pdf(payment), "application/pdf")],
    )
    if sent:
        Payment.objects.filter(pk=payment.pk).update(receipt_emailed_at=timezone.now())


def expire_stale_pending(hours=24):
    """Settle pending payments nobody came back for (daily job)."""
    cutoff = timezone.now() - timedelta(hours=hours)
    settled = 0
    for payment in Payment.objects.filter(status=Payment.PENDING, created_at__lt=cutoff, gateway="paystack"):
        status = verify_and_fulfil(payment, "daily-job")
        if status == Payment.PENDING:
            Payment.objects.filter(pk=payment.pk, status=Payment.PENDING).update(status=Payment.ABANDONED)
        settled += 1
    return settled
