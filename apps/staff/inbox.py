"""The staff inbox (apps.staff.models): who sees a thread, what is unread, and sending a message."""

from django.conf import settings
from django.db import transaction
from django.db.models import F, OuterRef, Q, Subquery
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.emails import send_email

from .models import StaffMessage, StaffThread, StaffThreadRead


def visible(user):
    """Threads sent to the admin's role, started by them or answered by them."""
    return StaffThread.objects.filter(
        Q(roles__contains=f",{user.role},") | Q(created_by=user) | Q(messages__author=user)
    ).distinct()


def unread(user):
    """Visible threads with a message the admin has not opened yet (their own messages count as read)."""
    seen = StaffThreadRead.objects.filter(thread=OuterRef("pk"), user=user).values("read_at")[:1]
    return visible(user).annotate(seen_at=Subquery(seen)).filter(Q(seen_at__isnull=True) | Q(seen_at__lt=F("last_message_at")))


def mark_read(thread, user):
    StaffThreadRead.objects.update_or_create(thread=thread, user=user, defaults={"read_at": timezone.now()})


def _recipients(thread, author):
    """Active admins in the thread's roles, its writer and everyone who answered, except the author."""
    answered = StaffMessage.objects.filter(thread=thread, author__isnull=False).values("author")
    return (
        User.objects.filter(is_staff=True, is_active=True)
        .filter(Q(staff_role__in=thread.role_list) | Q(pk=thread.created_by_id) | Q(pk__in=answered))
        .exclude(pk=author.pk)
        .distinct()
    )


@transaction.atomic
def start(author, *, subject, roles, body, customer=None, card=None):
    now = timezone.now()
    thread = StaffThread.objects.create(
        subject=subject.strip(), roles="," + ",".join(roles) + ",", customer=customer, card=card,
        created_by=author, created_at=now, last_message_at=now,
    )
    post(thread, author, body)
    return thread


@transaction.atomic
def post(thread, author, body):
    """Add a message, mark it read for its author and email everyone else on the thread."""
    now = timezone.now()
    message = StaffMessage.objects.create(thread=thread, author=author, body=body.strip(), created_at=now)
    StaffThread.objects.filter(pk=thread.pk).update(last_message_at=now)
    thread.last_message_at = now
    mark_read(thread, author)
    url = settings.SITE_URL + reverse("staff:message", args=[thread.pk])
    context = {"thread": thread, "message": message, "author": author.display_name or author.email, "url": url}
    for person in _recipients(thread, author):
        transaction.on_commit(lambda person=person: send_email(
            to=person.email, subject=f"Staff message: {thread.subject}", template="admin_staff_message",
            context=context, kind="staff_inbox", dedupe_key=f"staff-message:{message.pk}:{person.pk}",
        ))
    return message
