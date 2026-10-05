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
from django.utils import timezone, translation
from django.utils.translation import gettext

from apps.core.emails import notify_admins, send_email, subject_text
from apps.core.models import AuditLog, Counter, SiteSettings
from apps.core.utils import percent_of

from . import fx, paystack
from .models import Payment, Plan, PromoCode, Subscription

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Plans
# --------------------------------------------------------------------------


# The largest team the website sells to online; bigger companies contact the team.
TEAM_MAX_SEATS = 1000


def active_plans():
    return Plan.objects.filter(is_active=True).prefetch_related("seat_bands").order_by("sort_order", "price_minor")


def card_limit(user):
    """How many of this user's cards may be live: their team size or plan allowance."""
    subscription = getattr(user, "subscription", None)
    if subscription is not None:
        return subscription.card_limit
    return plan_for_limits(user).max_cards


def clamp_seats(plan, seats):
    """A team size the website will sell for ``plan`` (None for plans without teams)."""
    if plan is None or not plan.is_team:
        return None
    return min(plan.team_size(seats), TEAM_MAX_SEATS)


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


def _label_text(label, english=False):
    """A quote line's wording. A label may be a function that builds the text, so the same line can be
    written in English (kept with the payment, for the PDF receipt) and in the visitor's language."""
    if not callable(label):
        return str(label)
    if english:
        with translation.override("en"):
            return str(label())
    return str(label())


def build_quote(lines, promo=None):
    """lines: list of (label, amount_minor). Returns a quote dict."""
    subtotal = sum(amount for _label, amount in lines)
    discount = promo.discount_on(subtotal) if promo else 0
    taxable = subtotal - discount
    tax = tax_on(taxable)
    return {
        "lines": [
            {"label": _label_text(label, True), "display": _label_text(label), "amount_minor": amount}
            for label, amount in lines
        ],
        "subtotal_minor": subtotal,
        "discount_minor": discount,
        "tax_minor": tax,
        "total_minor": taxable + tax,
        "promo": promo,
        "tax_label": SiteSettings.load().tax_label,
    }


def upgrade_price(subscription, new_plan, seats=None):
    """PLN-01: the difference in yearly price, pro-rated by full months remaining.

    This also covers adding people to a team plan mid-year: the new team
    size's price less the current one, for the months left."""
    months = subscription.full_months_left
    difference = max(0, new_plan.price_for(seats) - subscription.yearly_price)
    return (difference * months) // subscription.plan.duration_months, months


def plan_label(plan, seats=None):
    """'Business plan for 12 people' on team plans, 'Basic plan' otherwise."""
    if plan.is_team:
        return gettext("%(plan)s plan for %(people)d people") % {"plan": plan.name, "people": plan.team_size(seats)}
    return gettext("%(plan)s plan") % {"plan": plan.name}


def subscription_quote(user, plan, promo=None, seats=None):
    """New subscription, renewal, or a renewal onto a different plan or team size."""
    subscription = user.subscription
    renewing = bool(subscription and subscription.expires_at > timezone.now())

    def label():
        text = gettext("%(plan)s, 12 more months") if renewing else gettext("%(plan)s, 12 months")
        return text % {"plan": plan_label(plan, seats)}

    return build_quote([(label, plan.price_for(seats))], promo)


# --------------------------------------------------------------------------
# Checkout
# --------------------------------------------------------------------------


def create_payment(
    *, user, purpose, quote, plan=None, months=0, nfc_order=None, seats=None,
    set_up_auto_renew=False, is_auto_renewal=False,
):
    rate = fx.latest_rate()
    return Payment.objects.create(
        user=user,
        reference=paystack.new_reference("NFC" if purpose == Payment.PURPOSE_NFC else "ADS"),
        purpose=purpose,
        plan=plan,
        months=months,
        seats=clamp_seats(plan, seats),
        nfc_order=nfc_order,
        subtotal_minor=quote["subtotal_minor"],
        discount_minor=quote["discount_minor"],
        tax_minor=quote["tax_minor"],
        amount_minor=quote["total_minor"],
        currency=settings.CURRENCY,
        promo_code=quote.get("promo"),
        line_items=[{"label": line["label"], "amount_minor": line["amount_minor"]} for line in quote["lines"]],
        fx_rate=rate.ghs_per_usd if rate else None,
        fx_rate_at=rate.fetched_at if rate else None,
        set_up_auto_renew=set_up_auto_renew,
        is_auto_renewal=is_auto_renewal,
    )


def checkout_url(payment, request, method=None):
    """Send the customer to Paystack's hosted page (Figure 4A).

    ``method`` is "card" or "momo" to show only that way of paying (the card page keeps the card for
    automatic renewal); None offers both. A zero-amount payment (a 100% promo code) is fulfilled straight away.
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
        channels={"card": ["card"], "momo": ["mobile_money"]}.get(method),
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
            remember_card(locked, data)
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
        # A dearer plan, or more people on a team plan, from now to the current expiry.
        subscription.plan = plan
        subscription.seats = clamp_seats(plan, payment.seats)
        subscription.pending_plan = None
        subscription.pending_plan_from = None
        subscription.pending_seats = None
        subscription.save()
        activate_cards(user, subscription)
        return subscription
    return activate(user, plan, payment.months or plan.duration_months, seats=payment.seats, anchor=payment.is_auto_renewal)


def activate(user, plan, months, seats=None, anchor=False):
    """Start or extend the account's subscription and bring its cards live.

    Renewing onto something cheaper while time remains (a cheaper plan, or a
    smaller team) is a downgrade: it waits for the current period to end
    (PLN-02). Renewing onto something dearer takes effect at once.
    """
    now = timezone.now()
    seats = clamp_seats(plan, seats)
    subscription = Subscription.objects.select_for_update().filter(user=user).first()
    if subscription is None:
        subscription = Subscription(user=user, plan=plan, seats=seats, started_at=now, expires_at=now)
        subscription.extend(months, anchor=anchor)
    else:
        still_running = subscription.expires_at > now and not subscription.cancelled_at
        if still_running and plan.price_for(seats) < subscription.yearly_price:
            subscription.pending_plan = plan
            subscription.pending_seats = seats
            subscription.pending_plan_from = subscription.expires_at
        else:
            subscription.plan = plan
            subscription.seats = seats
            subscription.pending_plan = None
            subscription.pending_seats = None
            subscription.pending_plan_from = None
        subscription.extend(months, anchor=anchor)
    subscription.save()
    activate_cards(user, subscription)
    return subscription


def activate_cards(user, subscription=None):
    """Draft cards go live on first activation; plan limits pick which (PLN-02)."""
    subscription = subscription or user.subscription
    if subscription is None:
        return
    limit = subscription.card_limit
    cards = list(user.cards.filter(deleted_at__isnull=True).order_by("created_at"))
    # Keep the owner's own choice while it fits the plan. If it no longer fits,
    # the oldest of the cards they had switched on stay live; a card they
    # switched off (say, for someone who left) is never switched back on.
    enabled = [c.pk for c in cards if c.is_enabled]
    keep = set(enabled[:limit]) if enabled else {c.pk for c in cards[:limit]}
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


def manual_activation(*, staff, user, plan, months, reason, amount_minor=0, seats=None):
    """ADM-01: cash payments, complimentary cards, failed webhooks."""
    seats = clamp_seats(plan, seats)
    with transaction.atomic():
        payment = Payment.objects.create(
            user=user,
            reference=paystack.new_reference("MAN"),
            purpose=Payment.PURPOSE_SUBSCRIPTION,
            plan=plan,
            months=months,
            seats=seats,
            subtotal_minor=amount_minor,
            amount_minor=amount_minor,
            gateway="manual",
            channel="cash" if amount_minor else "complimentary",
            manual_reason=reason,
            line_items=[{"label": f"{plan_label(plan, seats)}, {months} months (manual)", "amount_minor": amount_minor}],
        )
        fulfil(payment, {"status": "success", "channel": payment.channel, "fees": 0}, source="manual", actor=staff)
        AuditLog.record(staff, "manual_activation", user, reason=reason, plan=plan.code, months=months, seats=seats)
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
    subject = (
        subject_text("Your plan has renewed. Receipt %(number)s", number=payment.receipt_number)
        if payment.is_auto_renewal
        else subject_text("Your receipt %(number)s", number=payment.receipt_number)
    )
    sent = send_email(
        to=payment.user.email,
        subject=subject,
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


# --------------------------------------------------------------------------
# Automatic renewal by card
# --------------------------------------------------------------------------

# The card is charged on the day the plan ends; if that fails it is tried again 1, 3 and 5 days later,
# and then the usual grace period carries on as for any plan that was not renewed.
RENEWAL_RETRY_DAYS = (0, 1, 3, 5)
RENEWAL_NOTICE_DAYS = 7


def remember_card(payment, data):
    """Keep the card a customer paid with from the checkout page, so the plan renews itself.

    Paystack hands back a reusable token (``authorization``), never the card number. A card that cannot
    be charged again (``reusable`` false) simply leaves the customer on manual renewal.
    """
    if payment.is_auto_renewal:
        Subscription.objects.filter(user=payment.user).update(renew_attempts=0, last_renew_attempt_at=None)
        return False
    if not payment.set_up_auto_renew:
        return False
    auth = data.get("authorization") or {}
    code = auth.get("authorization_code")
    if not code or not auth.get("reusable") or (auth.get("channel") or "card") != "card":
        return False
    subscription = Subscription.objects.select_for_update().filter(user=payment.user).first()
    if subscription is None:
        return False
    expiry = ""
    if auth.get("exp_month") and auth.get("exp_year"):
        expiry = f"{auth['exp_month']}/{auth['exp_year']}"[:7]
    subscription.auto_renew = True
    subscription.authorization_code = str(code)[:64]
    subscription.authorization_email = str((data.get("customer") or {}).get("email") or payment.user.email)[:254]
    subscription.card_brand = str(auth.get("brand") or auth.get("card_type") or "").strip()[:24]
    subscription.card_last4 = str(auth.get("last4") or "")[-4:]
    subscription.card_expiry = expiry
    subscription.renew_attempts = 0
    subscription.last_renew_attempt_at = None
    subscription.auto_renew_stopped_at = None
    subscription.save(update_fields=[
        "auto_renew", "authorization_code", "authorization_email", "card_brand", "card_last4", "card_expiry",
        "renew_attempts", "last_renew_attempt_at", "auto_renew_stopped_at", "updated_at",
    ])
    return True


def renewal_due_date(subscription):
    """The day the plan ends, in Ghana time: the day the card is charged."""
    return timezone.localtime(subscription.expires_at).date()


def next_renewal(subscription):
    """What the next automatic renewal will be: (plan, seats, quote). A booked downgrade counts."""
    plan = subscription.pending_plan or subscription.plan
    seats = clamp_seats(plan, subscription.pending_seats if subscription.pending_plan_id else subscription.seats)

    def label():
        return gettext("%(plan)s, 12 months") % {"plan": plan_label(plan, seats)}

    return plan, seats, build_quote([(label, plan.price_for(seats))], None)


def attempt_renewal(subscription_id, now=None):
    """Charge the stored card for one more year, if it is due. Safe to call repeatedly.

    Returns "renewed", "failed", "pending" (Paystack has not settled it yet) or None (nothing due).
    Each attempt is recorded before the card is charged, so a re-run, or a crash half way, can never
    charge the same day twice.
    """
    now = now or timezone.now()
    today = timezone.localdate(now)
    with transaction.atomic():
        sub = Subscription.objects.select_for_update().select_related("user", "plan").filter(pk=subscription_id).first()
        if sub is None or not sub.renews_automatically or not sub.user.is_active or sub.user.anonymised_at:
            return None
        if now < sub.expires_at or sub.renew_attempts >= len(RENEWAL_RETRY_DAYS):
            return None
        if today < renewal_due_date(sub) + timedelta(days=RENEWAL_RETRY_DAYS[sub.renew_attempts]):
            return None
        if sub.last_renew_attempt_at and timezone.localdate(sub.last_renew_attempt_at) == today:
            return None
        plan, seats, quote = next_renewal(sub)
        payment = create_payment(
            user=sub.user, purpose=Payment.PURPOSE_SUBSCRIPTION, quote=quote, plan=plan,
            months=plan.duration_months, seats=seats, is_auto_renewal=True,
        )
        sub.renew_attempts += 1
        sub.last_renew_attempt_at = now
        sub.save(update_fields=["renew_attempts", "last_renew_attempt_at", "updated_at"])
        attempt, email, code = sub.renew_attempts, sub.authorization_email or sub.user.email, sub.authorization_code

    try:
        paystack.charge_authorization(
            email=email, amount_minor=payment.amount_minor, reference=payment.reference, authorization_code=code,
            metadata={"payment_id": payment.pk, "purpose": payment.purpose, "auto_renewal": True},
        )
    except paystack.PaymentError as exc:
        logger.warning("Automatic renewal %s was declined: %s", payment.reference, exc)
        Payment.objects.filter(pk=payment.pk, status=Payment.PENDING).update(
            status=Payment.FAILED, gateway_response={"error": str(exc)[:200]}
        )
        _renewal_failed(subscription_id, attempt)
        return "failed"
    status = verify_and_fulfil(payment, "auto-renewal")
    if status == Payment.SUCCESS:
        return "renewed"
    if status == Payment.PENDING:
        return "pending"
    _renewal_failed(subscription_id, attempt)
    return "failed"


def _renewal_failed(subscription_id, attempt):
    """Tell the customer the charge failed and when we will try again; after the last try, stop."""
    sub = Subscription.objects.select_related("user", "plan").get(pk=subscription_id)
    final = attempt >= len(RENEWAL_RETRY_DAYS)
    next_try = None if final else renewal_due_date(sub) + timedelta(days=RENEWAL_RETRY_DAYS[attempt])
    card = sub.card_label
    if final:
        Subscription.objects.filter(pk=sub.pk).update(
            auto_renew=False, authorization_code="", auto_renew_stopped_at=timezone.now()
        )
    send_email(
        to=sub.user.email, subject=subject_text("We could not renew your plan"), template="auto_renewal_failed",
        context={"user": sub.user, "subscription": sub, "next_try": next_try, "final": final, "card": card},
        user=sub.user, kind="auto_renewal_failed", dedupe_key=f"autofail:{sub.pk}:{sub.expires_at:%Y%m%d}:{attempt}",
    )


def _notice_date(day):
    from django.utils.formats import date_format

    return date_format(day, gettext("j F Y"))


def send_renewal_notices(today=None):
    """Tell each automatically renewing customer, 7 days ahead, what will be charged and when."""
    today = today or timezone.localdate()
    rate = fx.latest_rate()
    usd_rate = rate.ghs_per_usd if rate else None
    sent = 0
    subs = Subscription.objects.select_related("user", "plan").filter(
        auto_renew=True, cancelled_at__isnull=True, user__is_active=True
    ).exclude(authorization_code="")
    for sub in subs:
        days = (renewal_due_date(sub) - today).days
        if not 1 <= days <= RENEWAL_NOTICE_DAYS:
            continue
        plan, seats, quote = next_renewal(sub)
        previous = (
            Payment.objects.filter(user=sub.user, purpose=Payment.PURPOSE_SUBSCRIPTION, status=Payment.SUCCESS)
            .exclude(amount_minor=0).order_by("-paid_at").first()
        )
        changed = previous is not None and previous.amount_minor != quote["total_minor"]
        if send_email(
            to=sub.user.email,
            subject=subject_text("Your plan renews on %(date)s", date=_notice_date(renewal_due_date(sub))),
            template="auto_renewal_notice",
            context={
                "user": sub.user, "subscription": sub, "plan": plan, "seats": seats, "plan_label": plan_label(plan, seats),
                "amount_minor": quote["total_minor"], "previous_minor": previous.amount_minor if changed else None,
                "usd_rate": usd_rate, "charge_on": renewal_due_date(sub), "card": sub.card_label,
            },
            user=sub.user, kind="auto_renewal_notice", dedupe_key=f"autonotice:{sub.pk}:{sub.expires_at:%Y%m%d}",
        ):
            sent += 1
    return sent


def run_auto_renewals():
    """The daily job's step: every subscription whose card is due to be charged. Returns a summary."""
    counts = {"renewed": 0, "failed": 0, "pending": 0}
    ids = list(
        Subscription.objects.filter(auto_renew=True, cancelled_at__isnull=True, expires_at__lte=timezone.now())
        .exclude(authorization_code="").values_list("pk", flat=True)
    )
    for pk in ids:
        outcome = attempt_renewal(pk)
        if outcome:
            counts[outcome] += 1
    return counts


def cancel_auto_renew(subscription, actor=None):
    """Customer turns automatic renewal off. The plan keeps running until the end of what is paid for,
    and the stored card token is deleted."""
    card = subscription.card_label
    Subscription.objects.filter(pk=subscription.pk).update(
        auto_renew=False, authorization_code="", authorization_email="", renew_attempts=0,
        auto_renew_stopped_at=timezone.now(),
    )
    AuditLog.record(actor or subscription.user, "auto_renew_cancelled", subscription, card=card)
    subscription.refresh_from_db()
    send_email(
        to=subscription.user.email, subject=subject_text("Automatic renewal is off"), template="auto_renewal_cancelled",
        context={"user": subscription.user, "subscription": subscription, "card": card},
        user=subscription.user, kind="auto_renewal_cancelled",
    )
