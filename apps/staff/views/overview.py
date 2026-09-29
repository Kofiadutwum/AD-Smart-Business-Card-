"""Main admin dashboard (13.1)."""

from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.shortcuts import render
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import Payment, Subscription
from apps.cards.models import CardReport
from apps.core.models import SiteSettings
from apps.core.utils import usd_total
from apps.nfc.models import NFCOrder
from apps.support.models import SupportRequest

from ..permissions import can, staff_required


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
        "inactive": customers.count() - active - in_grace,
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
        paid_list = list(paid.only("amount_minor", "purpose", "fx_rate", "paid_at", "created_at"))
        subscription_kinds = {Payment.PURPOSE_SUBSCRIPTION, Payment.PURPOSE_UPGRADE}
        context.update(
            {
                "subscription_revenue_usd": usd_total((p.amount_minor, p.usd_rate) for p in paid_list if p.purpose in subscription_kinds),
                "nfc_revenue_usd": usd_total((p.amount_minor, p.usd_rate) for p in paid_list if p.purpose == Payment.PURPOSE_NFC),
                "month_revenue_usd": usd_total((p.amount_minor, p.usd_rate) for p in paid_list if p.paid_at and p.paid_at >= month_start),
            }
        )
        context.update(
            {
                "show_money": True,
                "pending_payments": Payment.objects.filter(status=Payment.PENDING).count(),
                "successful_payments": paid.count(),
                "subscription_revenue": paid.filter(purpose__in=[Payment.PURPOSE_SUBSCRIPTION, Payment.PURPOSE_UPGRADE]).aggregate(s=Sum("amount_minor"))["s"] or 0,
                "nfc_revenue": paid.filter(purpose=Payment.PURPOSE_NFC).aggregate(s=Sum("amount_minor"))["s"] or 0,
                "month_revenue": paid.filter(paid_at__gte=month_start).aggregate(s=Sum("amount_minor"))["s"] or 0,
            }
        )
    return render(request, "staff/home.html", context)
