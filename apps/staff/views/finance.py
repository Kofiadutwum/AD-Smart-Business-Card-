"""Payments, refunds, reports, promo codes (Section 15, ADM-05)."""

import csv
import io
from collections import OrderedDict
from datetime import datetime
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.billing import paystack
from apps.billing.models import Payment, PromoCode, Refund
from apps.core.models import AuditLog
from apps.core.utils import format_money, ghs_to_minor, to_usd, usd_total
from apps.nfc.models import NFCOrder
from apps.nfc.services import change_status

from ..forms import PromoCodeForm, RefundForm
from ..permissions import staff_required


def _filtered(request):
    qs = Payment.objects.select_related("user", "plan", "nfc_order", "promo_code").order_by("-created_at")
    status = request.GET.get("status", "")
    purpose = request.GET.get("purpose", "")
    q = request.GET.get("q", "").strip()
    start = request.GET.get("from", "")
    end = request.GET.get("to", "")
    if status:
        qs = qs.filter(status=status)
    if purpose:
        qs = qs.filter(purpose=purpose)
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(receipt_number__icontains=q) | Q(user__email__icontains=q))
    for value, lookup in ((start, "created_at__date__gte"), (end, "created_at__date__lte")):
        if value:
            try:
                qs = qs.filter(**{lookup: datetime.strptime(value, "%Y-%m-%d").date()})
            except ValueError:
                pass
    return qs


@staff_required("view_payments")
def payments(request):
    qs = _filtered(request)
    totals = qs.aggregate(
        amount=Sum("amount_minor", filter=Q(status=Payment.SUCCESS)),
        fees=Sum("gateway_fee_minor", filter=Q(status=Payment.SUCCESS)),
        count=Count("id"),
    )
    succeeded = list(qs.filter(status=Payment.SUCCESS).select_related(None).only("amount_minor", "gateway_fee_minor", "fx_rate", "paid_at", "created_at"))
    totals["amount_usd"] = usd_total((p.amount_minor, p.usd_rate) for p in succeeded)
    totals["fees_usd"] = usd_total((p.gateway_fee_minor, p.usd_rate) for p in succeeded)
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    return render(
        request, "staff/payments.html",
        {"page": page, "totals": totals, "statuses": Payment.STATUSES, "purposes": Payment.PURPOSES, "params": request.GET},
    )


EXPORT_HEADERS = [
    "Date", "Paid at", "Reference", "Receipt", "Customer", "Purpose", "Plan", "NFC order", "Subtotal GHS",
    "Discount GHS", "Promo", "Tax GHS", "Amount GHS", "Amount USD", "Paystack fee GHS", "Refunded GHS", "Status", "Method",
    "USD rate (GHS per USD, at payment)",
]


def _row(p):
    local = timezone.localtime
    return [
        local(p.created_at).strftime("%Y-%m-%d %H:%M"),
        local(p.paid_at).strftime("%Y-%m-%d %H:%M") if p.paid_at else "",
        p.reference, p.receipt_number or "", p.user.email, p.get_purpose_display(), p.plan.name if p.plan else "",
        p.nfc_order.order_number if p.nfc_order else "", p.subtotal_minor / 100, p.discount_minor / 100,
        p.promo_code.code if p.promo_code else "", p.tax_minor / 100, p.amount_minor / 100,
        _usd_cell(p.amount_minor, p.usd_rate), p.gateway_fee_minor / 100, p.refunded_minor / 100,
        p.get_status_display(), p.channel, str(p.usd_rate or ""),
    ]


def _usd_cell(minor, rate):
    value = to_usd(minor, rate)
    return float(round(value, 2)) if value is not None else ""


@staff_required("finance")
def payments_export(request, fmt):
    qs = _filtered(request).prefetch_related("refunds")
    stamp = timezone.localdate().isoformat()
    if fmt == "xlsx":
        from openpyxl import Workbook
        from openpyxl.styles import Font

        book = Workbook()
        sheet = book.active
        sheet.title = "Payments"
        sheet.append(EXPORT_HEADERS)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for payment in qs:
            sheet.append(_row(payment))
        buffer = io.BytesIO()
        book.save(buffer)
        AuditLog.record(request.user, "finance_export", reason="xlsx", rows=qs.count())
        return HttpResponse(
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="payments-{stamp}.xlsx"'},
        )
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="payments-{stamp}.csv"'
    response.write("﻿")
    writer = csv.writer(response)
    writer.writerow(EXPORT_HEADERS)
    for payment in qs:
        writer.writerow(_row(payment))
    AuditLog.record(request.user, "finance_export", reason="csv", rows=qs.count())
    return response


@staff_required("view_payments")
def payment_detail(request, pk):
    payment = get_object_or_404(Payment.objects.select_related("user", "plan", "nfc_order", "promo_code"), pk=pk)
    suggested = 0
    if payment.nfc_order:
        suggested = payment.nfc_order.refundable_minor
    return render(
        request, "staff/payment.html",
        {"payment": payment, "refunds": payment.refunds.all(), "refund_form": RefundForm(initial={"amount": suggested / 100 if suggested else None})},
    )


@staff_required("refunds")
@require_POST
def refund(request, pk):
    payment = get_object_or_404(Payment, pk=pk, status__in=[Payment.SUCCESS, Payment.PARTIALLY_REFUNDED])
    form = RefundForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter an amount and a reason.")
        return redirect("staff:payment", pk=pk)
    amount = ghs_to_minor(form.cleaned_data["amount"])
    available = payment.amount_minor - payment.refunded_minor
    if not 0 < amount <= available:
        messages.error(request, f"You can refund up to {format_money(available, payment.usd_rate)}.")
        return redirect("staff:payment", pk=pk)
    record = Refund.objects.create(payment=payment, amount_minor=amount, reason=form.cleaned_data["reason"], created_by=request.user)
    try:
        if payment.gateway == "paystack":
            data = paystack.refund(payment.reference, amount)
            record.gateway_reference = str(data.get("id") or "")
    except paystack.PaymentError as exc:
        record.status = Refund.FAILED
        record.save()
        messages.error(request, f"Paystack refused the refund: {exc}")
        return redirect("staff:payment", pk=pk)
    with transaction.atomic():
        record.status = Refund.DONE
        record.completed_at = timezone.now()
        record.save()
        payment.status = Payment.REFUNDED if payment.refunded_minor >= payment.amount_minor else Payment.PARTIALLY_REFUNDED
        payment.save(update_fields=["status"])
        AuditLog.record(request.user, "refund_issued", payment, reason=record.reason, amount_minor=amount)
        order = payment.nfc_order
        if order and order.status == NFCOrder.CANCELLED:
            change_status(order, NFCOrder.REFUNDED, request.user, note=f"Refund of {format_money(amount, payment.usd_rate)} issued.")
    messages.success(request, f"Refund of {format_money(amount, payment.usd_rate)} recorded.")
    return redirect("staff:payment", pk=pk)


@staff_required("finance")
def report(request):
    paid = Payment.objects.filter(status__in=[Payment.SUCCESS, Payment.PARTIALLY_REFUNDED, Payment.REFUNDED], paid_at__isnull=False)
    keys = ("subscription", "nfc", "delivery", "tax", "discount", "fees")
    months = OrderedDict()

    def add(bucket, key, minor, rate):
        # Cedis add up directly; dollars add up at each payment's own rate.
        bucket[key] += minor
        if minor and bucket[key + "_usd"] is not None:
            value = to_usd(minor, rate)
            bucket[key + "_usd"] = None if value is None else bucket[key + "_usd"] + value

    for p in paid.select_related("nfc_order").order_by("paid_at"):
        key = timezone.localtime(p.paid_at).strftime("%Y-%m")
        bucket = months.setdefault(key, {**{k: 0 for k in keys}, **{k + "_usd": Decimal(0) for k in keys}})
        rate = p.usd_rate
        if p.purpose == Payment.PURPOSE_NFC and p.nfc_order:
            add(bucket, "nfc", p.nfc_order.cards_minor, rate)
            add(bucket, "delivery", p.nfc_order.delivery_fee_minor, rate)
            add(bucket, "subscription", p.nfc_order.plan_minor, rate)
        elif p.purpose == Payment.PURPOSE_NFC:
            add(bucket, "nfc", p.amount_minor - p.tax_minor, rate)
        else:
            add(bucket, "subscription", p.subtotal_minor, rate)
        add(bucket, "tax", p.tax_minor, rate)
        add(bucket, "discount", p.discount_minor, rate)
        add(bucket, "fees", p.gateway_fee_minor, rate)
    rows = list(months.items())[-24:]
    peak = max((b["subscription"] + b["nfc"] + b["delivery"] for _, b in rows), default=0)
    totals = {k: sum(b[k] for _, b in rows) for k in keys}
    for k in keys:
        dollars = [b[k + "_usd"] for _, b in rows]
        totals[k + "_usd"] = None if any(d is None for d in dollars) else sum(dollars, Decimal(0))
    counts = dict(Payment.objects.values_list("status").annotate(n=Count("id")))
    promos = PromoCode.objects.annotate(
        discount=Sum("payment__discount_minor", filter=Q(payment__status=Payment.SUCCESS))
    ).order_by("-used_count")[:10]
    refunds = list(Refund.objects.filter(status=Refund.DONE).select_related("payment"))
    refunded = sum(r.amount_minor for r in refunds)
    refunded_usd = usd_total((r.amount_minor, r.usd_rate) for r in refunds)
    promos = list(promos)
    for promo in promos:
        used = Payment.objects.filter(promo_code=promo, status=Payment.SUCCESS).only("discount_minor", "fx_rate", "paid_at", "created_at")
        promo.discount_usd = usd_total((p.discount_minor, p.usd_rate) for p in used)
    return render(
        request, "staff/report.html",
        {"rows": rows, "peak": peak, "totals": totals, "counts": counts, "promos": promos,
         "refunded": refunded, "refunded_usd": refunded_usd},
    )


@staff_required("promo_codes")
def promo_codes(request):
    form = PromoCodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        promo = form.save(commit=False)
        promo.created_by = request.user
        promo.save()
        AuditLog.record(request.user, "promo_created", promo, kind=promo.kind, value=promo.value)
        messages.success(request, f"Promo code {promo.code} created.")
        return redirect("staff:promo_codes")
    return render(request, "staff/promo_codes.html", {"form": form, "codes": PromoCode.objects.all()})


@staff_required("promo_codes")
def promo_edit(request, pk):
    promo = get_object_or_404(PromoCode, pk=pk)
    form = PromoCodeForm(request.POST or None, instance=promo)
    if request.method == "POST" and form.is_valid():
        form.save()
        AuditLog.record(request.user, "promo_updated", promo)
        messages.success(request, "Promo code updated.")
        return redirect("staff:promo_codes")
    return render(request, "staff/form_page.html", {"form": form, "title": f"Edit {promo.code}", "back": "staff:promo_codes"})
