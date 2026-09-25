"""Support requests, card reports, the homepage gallery and subscriber email."""

from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.cards.models import CardReport
from apps.core.emails import send_email
from apps.core.models import AuditLog, SiteSettings
from apps.marketing.models import GalleryImage
from apps.support.forms import ReplyForm
from apps.support.models import SupportMessage, SupportRequest

from ..forms import BulkEmailForm, GalleryForm
from ..permissions import staff_required

# --------------------------------------------------------------------------
# Support (Section 17)
# --------------------------------------------------------------------------


@staff_required("support")
def support_list(request):
    status = request.GET.get("status", "open")
    qs = SupportRequest.objects.select_related("user").order_by("-priority", "-created_at")
    if status == "open":
        qs = qs.filter(status__in=[SupportRequest.OPEN, SupportRequest.IN_PROGRESS])
    elif status != "all":
        qs = qs.filter(status=status)
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "staff/support.html", {"page": page, "status": status, "statuses": SupportRequest.STATUSES})


@staff_required("support")
def support_detail(request, pk):
    ticket = get_object_or_404(SupportRequest, pk=pk)
    form = ReplyForm(request.POST or None)
    if request.method == "POST":
        new_status = request.POST.get("status")
        if form.is_valid():
            SupportMessage.objects.create(request=ticket, author=request.user, from_staff=True, body=form.cleaned_data["body"])
            send_email(
                to=ticket.email, subject=f"Re: {ticket.subject} ({ticket.reference})", template="support_reply",
                context={"ticket": ticket, "reply": form.cleaned_data["body"]}, user=ticket.user, kind="support_reply",
            )
            if ticket.status == SupportRequest.OPEN:
                ticket.status = SupportRequest.IN_PROGRESS
        if new_status in dict(SupportRequest.STATUSES) and new_status != ticket.status:
            ticket.status = new_status
            AuditLog.record(request.user, "support_status", ticket, new=new_status)
        ticket.assigned_to = ticket.assigned_to or request.user
        ticket.save()
        messages.success(request, "Saved." + (" The customer was emailed." if form.is_valid() else ""))
        return redirect("staff:support_detail", pk=pk)
    return render(
        request, "staff/support_detail.html",
        {"ticket": ticket, "thread": ticket.messages.select_related("author"), "form": ReplyForm(), "statuses": SupportRequest.STATUSES},
    )


# --------------------------------------------------------------------------
# Card reports (MOD-01, MOD-02)
# --------------------------------------------------------------------------


@staff_required("reports")
def reports(request):
    qs = CardReport.objects.select_related("card", "card__owner").order_by("status", "-created_at")
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "staff/reports.html", {"page": page})


@staff_required("reports")
@require_POST
def report_action(request, pk):
    item = get_object_or_404(CardReport, pk=pk)
    action = request.POST.get("action")
    if action in ("dismissed", "actioned"):
        item.status = action
        item.handled_by = request.user
        item.handled_at = timezone.now()
        item.save()
        AuditLog.record(request.user, f"report_{action}", item.card, reason=request.POST.get("reason", ""))
        messages.success(request, "Report updated.")
    return redirect("staff:reports")


# --------------------------------------------------------------------------
# Homepage gallery (the squeeze carousel)
# --------------------------------------------------------------------------


@staff_required("gallery")
def gallery(request):
    form = GalleryForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        image = form.save(commit=False)
        image.image.save(form.cleaned_data["processed"].name, form.cleaned_data["processed"], save=False)
        image.save()
        AuditLog.record(request.user, "gallery_added", image)
        messages.success(request, f"“{image.title}” is now on the homepage.")
        return redirect("staff:gallery")
    return render(request, "staff/gallery.html", {"form": form, "images": GalleryImage.objects.all()})


@staff_required("gallery")
def gallery_edit(request, pk):
    image = get_object_or_404(GalleryImage, pk=pk)
    form = GalleryForm(request.POST or None, request.FILES or None, instance=image)
    if request.method == "POST" and form.is_valid():
        image = form.save(commit=False)
        if form.cleaned_data.get("processed"):
            old = image.image.name
            image.image.save(form.cleaned_data["processed"].name, form.cleaned_data["processed"], save=False)
            if old:
                image.image.storage.delete(old)
        image.save()
        AuditLog.record(request.user, "gallery_updated", image)
        messages.success(request, "Slide updated.")
        return redirect("staff:gallery")
    return render(request, "staff/gallery_edit.html", {"form": form, "image": image})


@staff_required("gallery")
@require_POST
def gallery_action(request, pk):
    image = get_object_or_404(GalleryImage, pk=pk)
    action = request.POST.get("action")
    if action == "toggle":
        image.is_published = not image.is_published
        image.save(update_fields=["is_published"])
        messages.info(request, f"Slide {'published' if image.is_published else 'hidden'}.")
    elif action in ("up", "down"):
        ordered = list(GalleryImage.objects.all())
        index = ordered.index(image)
        swap = index - 1 if action == "up" else index + 1
        if 0 <= swap < len(ordered):
            ordered[index], ordered[swap] = ordered[swap], ordered[index]
            for position, item in enumerate(ordered):
                GalleryImage.objects.filter(pk=item.pk).update(display_order=position)
    elif action == "delete":
        AuditLog.record(request.user, "gallery_deleted", image)
        if image.image:
            image.image.delete(save=False)
        image.delete()
        messages.info(request, "Slide deleted.")
    return redirect("staff:gallery")


# --------------------------------------------------------------------------
# Email subscribers (ADM-02)
# --------------------------------------------------------------------------


def _audience(key, email=None):
    customers = User.objects.filter(is_staff=False, is_active=True, anonymised_at__isnull=True)
    now = timezone.now()
    grace = timedelta(days=SiteSettings.load().grace_period_days)
    if key == "one":
        return customers.filter(email__iexact=email)
    if key == "active":
        return customers.filter(subscription_record__expires_at__gte=now - grace)
    if key == "expiring":
        return customers.filter(subscription_record__expires_at__gte=now, subscription_record__expires_at__lte=now + timedelta(days=30))
    if key == "inactive":
        return customers.filter(subscription_record__expires_at__lt=now - grace)
    return customers


@staff_required("email_subscribers")
def email_subscribers(request):
    form = BulkEmailForm(request.POST or None, initial={"email": request.GET.get("email", ""), "audience": "one" if request.GET.get("email") else "active"})
    recipients = None
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        recipients = list(_audience(data["audience"], data.get("email")))
        if request.POST.get("confirm") == "yes":
            sent = 0
            for user in recipients:
                if send_email(
                    to=user.email, subject=data["subject"], template="staff_message",
                    context={"user": user, "body": data["body"]}, user=user, kind="staff_message", sent_by=request.user,
                ):
                    sent += 1
            AuditLog.record(request.user, "bulk_email", reason=data["subject"], audience=data["audience"], sent=sent)
            messages.success(request, f"Sent to {sent} of {len(recipients)} recipients.")
            return redirect("staff:email")
    return render(request, "staff/email.html", {"form": form, "recipients": recipients})
