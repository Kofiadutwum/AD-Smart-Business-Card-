"""Contact and support requests (Section 17)."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.emails import notify_admins, send_email
from apps.core.utils import client_ip, rate_limited

from .forms import ContactForm, ReplyForm
from .models import SupportMessage, SupportRequest


def contact(request):
    user = request.user if request.user.is_authenticated else None
    initial = {}
    if user:
        initial = {"name": user.display_name, "email": user.email, "phone": user.phone or ""}
    if request.GET.get("topic"):
        initial["category"] = request.GET["topic"]
    form = ContactForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        if rate_limited(f"contact:{client_ip(request)}", 6, 3600):
            messages.error(request, "You have sent several messages already. We will get back to you soon.")
            return redirect("support:contact")
        ticket = form.save(commit=False)
        ticket.user = user
        subscription = getattr(user, "subscription", None) if user else None
        ticket.priority = bool(subscription and subscription.is_live and subscription.plan.priority_support)
        ticket.save()
        SupportMessage.objects.create(request=ticket, author=user, body=form.cleaned_data["message"])
        send_email(
            to=ticket.email, subject=f"We received your message ({ticket.reference})", template="support_received",
            context={"ticket": ticket}, user=user, kind="support_received",
        )
        notify_admins(
            f"{'PRIORITY · ' if ticket.priority else ''}Support request {ticket.reference}: {ticket.subject}",
            "admin_support_request",
            {"ticket": ticket, "message": form.cleaned_data["message"],
             "staff_url": f"{settings.SITE_URL}/staff/support/{ticket.pk}/"},
        )
        messages.success(request, f"Thanks — your reference is {ticket.reference}. We reply within 1 working day.")
        return redirect("support:mine" if user else "support:contact")
    return render(request, "support/contact.html", {"form": form})


@login_required
def mine(request):
    tickets = request.user.support_requests.all()
    return render(request, "support/mine.html", {"tickets": tickets, "active": "support"})


@login_required
def ticket(request, reference):
    item = get_object_or_404(SupportRequest, reference=reference, user=request.user)
    return render(request, "support/ticket.html", {"ticket": item, "form": ReplyForm(), "active": "support"})


@login_required
@require_POST
def reply(request, reference):
    item = get_object_or_404(SupportRequest, reference=reference, user=request.user)
    form = ReplyForm(request.POST)
    if form.is_valid():
        SupportMessage.objects.create(request=item, author=request.user, body=form.cleaned_data["body"])
        if item.status in (SupportRequest.RESOLVED, SupportRequest.CLOSED):
            item.status = SupportRequest.OPEN
        item.save()
        notify_admins(f"Customer replied on {item.reference}", "admin_support_request",
                      {"ticket": item, "message": form.cleaned_data["body"],
                       "staff_url": f"{settings.SITE_URL}/staff/support/{item.pk}/"})
        messages.success(request, "Reply sent.")
    return redirect("support:ticket", reference=reference)
