"""Main admin dashboard (13.1)."""

import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from datetime import timezone as dt_timezone

from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import Payment, Subscription
from apps.cards.models import CardEvent, CardReport
from apps.core.models import SiteSettings
from apps.core.utils import format_ghs, usd_total
from apps.nfc.models import NFCOrder
from apps.support.models import SupportRequest

from ..permissions import can, staff_required
from ..regions import REGIONS, cards_q, customers_q, orders_q, payments_q, update_owners

log = logging.getLogger(__name__)

# The Overview graphs. Orders still waiting for payment are left out.
ORDER_STAGES = (
    ("New", (NFCOrder.PAYMENT_CONFIRMED, NFCOrder.DESIGN_PENDING)),
    ("In design", (NFCOrder.DESIGN_IN_PROGRESS, NFCOrder.AWAITING_APPROVAL, NFCOrder.REVISION_REQUESTED, NFCOrder.ON_HOLD)),
    ("In production", (NFCOrder.APPROVED, NFCOrder.PRINTING, NFCOrder.ENCODING_QC, NFCOrder.READY_FOR_DELIVERY, NFCOrder.READY_FOR_PICKUP, NFCOrder.DISPATCHED)),
    ("Delivered", (NFCOrder.DELIVERED,)),
    ("Cancelled or refunded", (NFCOrder.CANCELLED, NFCOrder.REFUNDED)),
)
SUPPORT_STAGES = (
    ("Open", (SupportRequest.OPEN, SupportRequest.IN_PROGRESS)),
    ("Resolved", (SupportRequest.RESOLVED, SupportRequest.CLOSED)),
)
CARD_ACTIVITY = (("views", (CardEvent.VIEW,)), ("saves", (CardEvent.SAVE_CONTACT,)), ("taps", ()))  # taps: every other kind
# Clicking a status bar opens the list it belongs to: the NFC orders queue nearest each stage, then the support filter.
STAGE_LISTS = (
    *(("staff:nfc_orders", "nfc_view", f"?queue={queue}") for queue in ("new", "design", "production", "done", "closed")),
    *(("staff:support", "support", f"?status={status}") for status in ("open", "resolved")),
)


def _per_day(queryset, field, since, group=None, total=None):
    """{group value: {day: n}}, counted (or summed) by the database: one row per day and group."""
    # shortcut: Accra is UTC+0 all year, so a UTC day is an Accra day and MySQL needs no time zone tables; bucket in Python if TIME_ZONE ever moves.
    rows = (
        queryset.filter(**{f"{field}__gte": since})
        .annotate(day=TruncDate(field, tzinfo=dt_timezone.utc))
        .values("day", *([group] if group else []))
        .annotate(n=total or Count("pk"))
        .order_by()
    )
    days = defaultdict(lambda: defaultdict(int))
    for row in rows:
        days[row[group] if group else None][row["day"]] += row["n"] or 0
    return days


def _regroup(days, groups, rest=None):
    """Folds per-status (or per-kind) days into named groups; values in no group go to `rest`, or are dropped."""
    lookup = {value: name for name, values in groups for value in values}
    out = {name: defaultdict(int) for name, _values in groups}
    for value, per in days.items():
        name = lookup.get(value, rest)
        for day, n in per.items() if name else ():
            out[name][day] += n
    return out


def _months(today):
    """The first day of each of the last 12 months, this one included."""
    index = today.year * 12 + today.month - 1
    return [date(i // 12, i % 12 + 1, 1) for i in range(index - 11, index + 1)]


def _timeline(series, today):
    """Each series per day over 7 and 30 days, and per month over the last 12 months."""
    spans = {}
    for span, length in (("7d", 7), ("30d", 30)):
        days = [today - timedelta(days=i) for i in range(length - 1, -1, -1)]
        spans[span] = {"labels": [d.isoformat() for d in days], "values": {key: [per.get(d, 0) for d in days] for key, per in series.items()}}
    months = _months(today)
    spans["12m"] = {
        "labels": [m.strftime("%Y-%m") for m in months],
        "values": {key: [sum(n for d, n in per.items() if (d.year, d.month) == (m.year, m.month)) for m in months] for key, per in series.items()},
    }
    return spans


def _charts(customers, show_money, now, user):
    """The four Overview graphs, worked out for every span and region at once so the switches need no reload.
    A graph's ``link`` opens the list behind a bar or point ({from}, {to} and {region} filled in by the page),
    given only to roles that may open that list."""
    today = timezone.localdate(now)
    months = _months(today)
    since = timezone.make_aware(datetime.combine(months[0], time.min))
    starts = {"7d": today - timedelta(days=6), "30d": today - timedelta(days=29), "12m": months[0]}

    def each_region(build):
        # "all" counts everyone, including customers with no plan yet, who are in neither region.
        return {region: build(region, (lambda make: Q() if region == "all" else make(region))) for region in ("all", *dict(REGIONS))}

    def link(name, permission, query):
        return reverse(name) + query if can(user, permission) else None

    def counts(groups, start):
        return [sum(n for d, n in per.items() if d >= start) for per in groups.values()]

    def work(region, scope):
        orders = _regroup(_per_day(NFCOrder.objects.filter(scope(orders_q)), "created_at", since, group="status"), ORDER_STAGES)
        support = _regroup(_per_day(SupportRequest.objects.filter(scope(lambda r: customers_q(r, "user__"))), "created_at", since, group="status"), SUPPORT_STAGES)
        return {
            span: {
                "labels": [*orders, *support],
                "values": {"orders": [*counts(orders, start), *[None] * len(support)], "support": [*[None] * len(orders), *counts(support, start)]},
            }
            for span, start in starts.items()
        }

    charts = []
    if show_money:
        paid, net = Payment.objects.filter(status=Payment.SUCCESS), Sum(F("amount_minor") - F("processing_fee_minor"))
        charts.append({
            "id": "revenue", "title": "Revenue", "kind": "bar", "money": True, "wide": True, "span": "12m", "total": "received",
            "empty": "No payments in this period.", "series": [{"key": "revenue", "label": "Revenue"}],
            "link": link("staff:payments", "view_payments", "?status=success&from={from}&to={to}&region={region}"),
            "data": each_region(lambda region, scope: _timeline({"revenue": _per_day(paid.filter(scope(payments_q)), "paid_at", since, total=net)[None]}, today)),
        })
    charts.append({
        "id": "signups", "title": "New sign-ups", "kind": "line", "span": "30d", "total": "new customers",
        "empty": "No sign-ups in this period.", "series": [{"key": "signups", "label": "Sign-ups"}],
        "link": link("staff:subscribers", "view_subscribers", "?from={from}&to={to}&region={region}"),
        "data": each_region(lambda region, scope: _timeline({"signups": _per_day(customers.filter(scope(customers_q)), "date_joined", since)[None]}, today)),
    })
    charts.append({
        "id": "work", "title": "NFC orders and support", "kind": "bar", "categories": True, "span": "30d", "total": "NFC orders",
        "empty": "No NFC orders or support requests in this period.",
        "series": [{"key": "orders", "label": "NFC orders"}, {"key": "support", "label": "Support requests"}],
        "links": [link(*stage) for stage in STAGE_LISTS],
        "data": each_region(work),
    })
    charts.append({
        "id": "activity", "title": "Card views and taps", "kind": "line", "wide": True, "span": "30d", "total": "card views",
        "empty": "No card activity in this period.",
        "series": [{"key": "views", "label": "Views"}, {"key": "saves", "label": "Contact saves"}, {"key": "taps", "label": "Button taps"}],
        "data": each_region(lambda region, scope: _timeline(_regroup(
            _per_day(CardEvent.objects.filter(scope(lambda r: cards_q(r, "card__"))), "occurred_at", since, group="kind"), CARD_ACTIVITY, rest="taps"
        ), today)),
    })
    for chart in charts:
        chart["rows"] = _rows(chart)
    return charts


def _rows(chart):
    """The first span shown, as table rows, so the figures read even without scripting."""
    data = chart["data"]["all"][chart["span"]]
    rows = []
    for i, label in enumerate(data["labels"]):
        if not chart.get("categories"):
            parts = [int(p) for p in label.split("-")]
            label = date(*parts).strftime("%a %d %b") if len(parts) == 3 else date(*parts, 1).strftime("%B %Y")
        values = [data["values"][s["key"]][i] for s in chart["series"]]
        rows.append((label, ["" if v is None else format_ghs(v) if chart.get("money") else v for v in values]))
    return rows


@staff_required()
def home(request):
    now = timezone.now()
    grace = timedelta(days=SiteSettings.load().grace_period_days)
    customers = User.objects.filter(is_staff=False, anonymised_at__isnull=True)
    subs = Subscription.objects.filter(cancelled_at__isnull=True)
    active = subs.filter(expires_at__gte=now).count()
    in_grace = subs.filter(expires_at__lt=now, expires_at__gte=now - grace).count()
    context = {
        "subscribers": customers.count(),
        "active": active,
        "in_grace": in_grace,
        "inactive": customers.count() - active - in_grace - customers.filter(subscription_record__isnull=True, pk__in=update_owners()).count(),
        "pending_orders": NFCOrder.objects.filter(status__in=[NFCOrder.PAYMENT_CONFIRMED, NFCOrder.DESIGN_PENDING]).count(),
        "waiting_orders": NFCOrder.objects.filter(status__in=[NFCOrder.AWAITING_APPROVAL, NFCOrder.ON_HOLD, NFCOrder.REVISION_REQUESTED]).count(),
        "open_support": SupportRequest.objects.filter(status__in=[SupportRequest.OPEN, SupportRequest.IN_PROGRESS]).count(),
        "open_reports": CardReport.objects.filter(status=CardReport.OPEN).count(),
        "recent_users": customers.order_by("-date_joined")[:8],
        "expiring": subs.filter(expires_at__gte=now, expires_at__lte=now + timedelta(days=30)).select_related("user", "plan").order_by("expires_at")[:10],
        "flagged": Payment.objects.exclude(flag="").filter(status=Payment.PENDING)[:5],
    }
    if can(request.user, "finance") or can(request.user, "view_payments"):
        paid = Payment.objects.filter(status=Payment.SUCCESS)
        month_start = timezone.localtime(now).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # Dollars at each payment's own rate, added up (never today's rate on old money).
        paid_list = list(paid.only("amount_minor", "processing_fee_minor", "purpose", "fx_rate", "paid_at", "created_at"))
        subscription_kinds = {Payment.PURPOSE_SUBSCRIPTION, Payment.PURPOSE_UPGRADE, Payment.PURPOSE_CARD_UPDATE}
        # Revenue is the price; the processing fee the customer paid on top goes straight to Paystack.
        context.update(
            {
                "subscription_revenue_usd": usd_total((p.amount_minor - p.processing_fee_minor, p.usd_rate) for p in paid_list if p.purpose in subscription_kinds),
                "nfc_revenue_usd": usd_total((p.amount_minor - p.processing_fee_minor, p.usd_rate) for p in paid_list if p.purpose == Payment.PURPOSE_NFC),
                "premade_revenue_usd": usd_total((p.amount_minor - p.processing_fee_minor, p.usd_rate) for p in paid_list if p.purpose == Payment.PURPOSE_PREMADE),
                "month_revenue_usd": usd_total((p.amount_minor - p.processing_fee_minor, p.usd_rate) for p in paid_list if p.paid_at and p.paid_at >= month_start),
            }
        )
        context.update(
            {
                "show_money": True,
                "pending_payments": Payment.objects.filter(status=Payment.PENDING).count(),
                "successful_payments": paid.count(),
                "subscription_revenue": paid.filter(purpose__in=[Payment.PURPOSE_SUBSCRIPTION, Payment.PURPOSE_UPGRADE, Payment.PURPOSE_CARD_UPDATE]).aggregate(s=Sum(F("amount_minor") - F("processing_fee_minor")))["s"] or 0,
                "nfc_revenue": paid.filter(purpose=Payment.PURPOSE_NFC).aggregate(s=Sum(F("amount_minor") - F("processing_fee_minor")))["s"] or 0,
                "premade_revenue": paid.filter(purpose=Payment.PURPOSE_PREMADE).aggregate(s=Sum(F("amount_minor") - F("processing_fee_minor")))["s"] or 0,
                "month_revenue": paid.filter(paid_at__gte=month_start).aggregate(s=Sum(F("amount_minor") - F("processing_fee_minor")))["s"] or 0,
            }
        )
        # Each money figure again for Ghana and outside Ghana: separate accounts, as the prices differ.
        tiles = {
            "month": Q(paid_at__gte=month_start),
            "subscription": Q(purpose__in=subscription_kinds),
            "nfc": Q(purpose=Payment.PURPOSE_NFC),
            "premade": Q(purpose=Payment.PURPOSE_PREMADE),
        }
        split = paid.aggregate(**{
            f"{tile}_{region}": Sum(F("amount_minor") - F("processing_fee_minor"), filter=q & payments_q(region))
            for tile, q in tiles.items() for region in dict(REGIONS)
        })
        context["split"] = {key: value or 0 for key, value in split.items()}
    try:  # the graphs are extra: a failure shows their error state, never a broken Overview
        with transaction.atomic():
            context["charts"] = _charts(customers, context.get("show_money", False), now, request.user)
    except Exception:
        log.exception("Overview graphs failed")
        context["charts_failed"] = True
    return render(request, "staff/home.html", context)
