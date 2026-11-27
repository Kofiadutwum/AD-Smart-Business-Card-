"""The staff inbox: messages between admins, sent to the roles the writer picks, often about a card."""

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import User
from apps.cards.models import Card

from .. import inbox as service
from ..forms import StaffMessageForm, StaffReplyForm
from ..permissions import staff_required


def _number(value):
    return int(value) if (value or "").isdigit() else None


@staff_required("staff_messages")
def inbox(request):
    threads = service.visible(request.user).select_related("customer", "card", "created_by")
    unread = set(service.unread(request.user).values_list("pk", flat=True))
    page = Paginator(threads, 30).get_page(request.GET.get("page"))
    return render(request, "staff/inbox.html", {"page": page, "unread": unread, "role_names": dict(User.STAFF_ROLES)})


@staff_required("staff_messages")
def new(request):
    """Write to other admins, about a card (?card=) or a customer (?customer=) when started from their page."""
    card_pk = _number(request.POST.get("card") or request.GET.get("card"))
    card = Card.objects.select_related("owner").filter(pk=card_pk, owner__is_staff=False).first() if card_pk else None
    customer_pk = _number(request.POST.get("customer") or request.GET.get("customer"))
    customer = card.owner if card else (User.objects.filter(pk=customer_pk, is_staff=False).first() if customer_pk else None)
    initial = {"subject": f"About {card.full_name}" if card else (f"About {customer.display_name}" if customer else "")}
    form = StaffMessageForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        thread = service.start(
            request.user, subject=data["subject"], roles=data["to"], body=data["body"], customer=customer, card=card
        )
        messages.success(request, "Message sent. Everyone it went to has been emailed.")
        return redirect("staff:message", pk=thread.pk)
    return render(request, "staff/message_new.html", {"form": form, "card": card, "customer": customer})


@staff_required("staff_messages")
def thread(request, pk):
    thread = get_object_or_404(service.visible(request.user).select_related("customer", "card", "created_by"), pk=pk)
    form = StaffReplyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        service.post(thread, request.user, form.cleaned_data["body"])
        messages.success(request, "Reply sent.")
        return redirect("staff:message", pk=thread.pk)
    service.mark_read(thread, request.user)
    return render(request, "staff/message.html", {
        "thread": thread, "thread_messages": thread.messages.select_related("author"), "form": form,
        "role_names": [dict(User.STAFF_ROLES).get(role, role) for role in thread.role_list],
    })
