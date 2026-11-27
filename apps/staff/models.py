"""The staff inbox: messages between admins, each thread sent to one or more staff roles and, when it is
about one, linked to a customer or a card (owner, 27 Nov 2026: the Sales Admin writes to the other admins
about cards). Everyone in those roles, the writer and anyone who has answered reads the thread and replies."""

from django.conf import settings
from django.db import models
from django.utils import timezone


class StaffThread(models.Model):
    subject = models.CharField(max_length=160)
    # The roles it was sent to, written ",super,design," so one role is found with roles__contains=",design,".
    roles = models.CharField(max_length=80)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    card = models.ForeignKey("cards.Card", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    last_message_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-last_message_at"]

    def __str__(self):
        return self.subject

    @property
    def role_list(self):
        return [role for role in self.roles.split(",") if role]


class StaffMessage(models.Model):
    thread = models.ForeignKey(StaffThread, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    body = models.TextField(max_length=5000)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]


class StaffThreadRead(models.Model):
    """When an admin last opened a thread, for the unread count in the staff menu."""

    thread = models.ForeignKey(StaffThread, on_delete=models.CASCADE, related_name="reads")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    read_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["thread", "user"], name="staff_thread_read_once")]
