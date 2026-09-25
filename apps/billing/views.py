"""Checkout, payment return, webhook and receipts (Figures 2B, 2C, 4A-4C)."""

import logging

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.decorators import customer_required
from apps.core.emails import notify_admins
from apps.core.utils import format_ghs

from . import fx, paystack, services
from .models import Payment, Plan
from .receipts import render_receipt_pdf

logger = logging.getLogger(__name__)


def _plan_from(request, default=None):
    code = request.POST.get("plan") or request.GET.get("plan") or default
    return Plan.objects.filter(code=code, is_active=True).first()


@customer_required
def checkout(request):
    """New subscription or renewal (adds 12 months from the current expiry)."""
    user = request.user
    subscription = user.subscription
    plans = list(services.active_plans())
    if not plans:
        messages.error(request, "Plans are not set up yet. Please contact support.")
        return redirect("dashboard:home")
    default_code = (
        subscription.plan.code
        if subscription
        else request.session.get("intended_plan") or next((p.code for p in plans if p.is_featured), plans[0].code)
    )
    plan = _plan_from(request, default_code) or plans[0]

    promo_code = (request.POST.get("promo") or request.GET.get("promo") or "").strip()
    promo, promo_error = services.resolve_promo(promo_code, "subscription")
    quote = services.subscription_quote(user, plan, promo)
    upgrade = None
    if subscription and subscription.is_live and plan.price_minor > subscription.plan.price_minor:
        amount, months = services.upgrade_price(subscription, plan)
        if months > 0:
            upgrade = {"amount_minor": amount, "months": months}

    if request.method == "POST" and request.POST.get("action") in ("pay", "upgrade"):
        if promo_error:
            messages.error(request, promo_error)
        elif not request.POST.get("accept_policy"):
            messages.error(request, "Please tick the box to accept the Terms and Refund Policy before paying.")
        elif not user.cards.filter(deleted_at__isnull=True).exists():
            messages.error(request, "Create your card before choosing a plan.")
            return redirect("dashboard:editor")
        else:
            if request.POST["action"] == "upgrade" and upgrade:
                upgrade_quote = services.build_quote(
                    [(f"Upgrade to {plan.name} for {upgrade['months']} remaining months", upgrade["amount_minor"])],
                    promo,
                )
                payment = services.create_payment(
                    user=user, purpose=Payment.PURPOSE_UPGRADE, quote=upgrade_quote, plan=plan,
                )
            else:
                payment = services.create_payment(
                    user=user, purpose=Payment.PURPOSE_SUBSCRIPTION, quote=quote, plan=plan,
                    months=plan.duration_months,
                )
            try:
                return redirect(services.checkout_url(payment, request))
            except paystack.PaymentError as exc:
                Payment.objects.filter(pk=payment.pk).update(status=Payment.FAILED)
                messages.error(request, f"{exc} You have not been charged.")
    elif promo_code and promo_error:
        messages.error(request, promo_error)
    elif promo:
        messages.success(request, f"Promo code {promo.code} applied: {promo.label}.")

    return render(
        request,
        "billing/checkout.html",
        {
            "active": "billing",
            "plans": plans,
            "plan": plan,
            "subscription": subscription,
            "quote": quote,
            "pay_label": f"Pay {format_ghs(quote['total_minor'])}",
            "upgrade": upgrade,
            "promo_code": promo.code if promo else promo_code,
            "rate": fx.latest_rate(),
            "pending_downgrade": bool(
                subscription and subscription.expires_at > timezone.now()
                and plan.price_minor < subscription.plan.price_minor
            ),
        },
    )


def callback(request):
    """Where Paystack's hosted page sends the customer back (Figure 2C)."""
    reference = request.GET.get("reference") or request.GET.get("trxref")
    payment = Payment.objects.filter(reference=reference).first() if reference else None
    if payment is None:
        messages.error(request, "We could not find that payment. If money left your account, contact support with the reference.")
        return redirect("dashboard:home" if request.user.is_authenticated else "marketing:home")
    services.verify_and_fulfil(payment, "callback")
    return redirect("billing:result", reference=payment.reference)


def result(request, reference):
    payment = get_object_or_404(Payment, reference=reference)
    if request.user.is_authenticated and payment.user_id != request.user.pk and not request.user.is_staff:
        raise Http404
    if payment.status == Payment.PENDING and request.GET.get("check"):
        services.verify_and_fulfil(payment, "result-page")
        payment.refresh_from_db()
    owner = request.user.is_authenticated and payment.user_id == request.user.pk
    return render(request, "billing/result.html", {"payment": payment, "owner": owner})


@csrf_exempt
@require_POST
def webhook(request):
    """Paystack's server-to-server notice (PAY-05, PAY-06).

    The HMAC signature authenticates the request (not CSRF). Even a valid
    event is re-verified with the Verify API before anything changes.
    """
    raw = request.body
    if not settings.PAYMENT_SANDBOX and not paystack.signature_is_valid(raw, request.headers.get("x-paystack-signature", "")):
        logger.warning("Rejected webhook with a bad signature")
        notify_admins(
            "Unverified Paystack webhook rejected", "admin_webhook_rejected",
            {"size": len(raw)}, dedupe_key=f"webhook-bad:{timezone.now():%Y%m%d%H}",
        )
        return HttpResponse(status=401)
    try:
        event = paystack.parse_event(raw)
    except paystack.PaymentError:
        return HttpResponse(status=400)
    if event.get("event") not in ("charge.success", "transaction.success"):
        return HttpResponse(status=200)
    reference = (event.get("data") or {}).get("reference")
    payment = Payment.objects.filter(reference=reference).first()
    if payment is None:
        return HttpResponse(status=200)  # not ours; acknowledge so Paystack stops retrying
    services.verify_and_fulfil(payment, "webhook")
    return HttpResponse(status=200)


@customer_required
def receipts(request):
    payments = request.user.payments.select_related("plan", "nfc_order").order_by("-created_at")
    return render(request, "billing/receipts.html", {"payments": payments, "active": "receipts"})


def receipt_pdf(request, reference):
    if not request.user.is_authenticated:
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")
    payment = get_object_or_404(Payment, reference=reference, status__in=[Payment.SUCCESS, Payment.REFUNDED, Payment.PARTIALLY_REFUNDED])
    if payment.user_id != request.user.pk and not request.user.is_staff:
        raise Http404
    return HttpResponse(
        render_receipt_pdf(payment),
        content_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{payment.receipt_number}.pdf"'},
    )
