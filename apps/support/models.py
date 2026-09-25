"""Customer support requests (Section 17)."""

import secrets

from django.conf import settings
from django.db import models


class SupportRequest(models.Model):
    CATEGORIES = [
        ("account", "Account issue"),
        ("payment", "Payment issue"),
        ("card", "Digital card issue"),
        ("nfc", "NFC order issue"),
        ("design", "Design request"),
        ("other", "Other"),
    ]
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"
    STATUSES = [(OPEN, "Open"), (IN_PROGRESS, "In progress"), (RESOLVED, "Resolved"), (CLOSED, "Closed")]

    reference = models.CharField(max_length=16, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="support_requests"
    )
    name = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=32, blank=True)
    category = models.CharField(max_length=16, choices=CATEGORIES)
    subject = models.CharField(max_length=160)
    status = models.CharField(max_length=16, choices=STATUSES, default=OPEN, db_index=True)
    priority = models.BooleanField(default=False, help_text="Business plan customers.")
    nfc_order = models.ForeignKey("nfc.NFCOrder", null=True, blank=True, on_delete=models.SET_NULL)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reference} {self.subject}"

    def save(self, *args, **kwargs):
        if not self.reference:
            while True:
                candidate = "SR-" + secrets.token_hex(3).upper()
                if not SupportRequest.objects.filter(reference=candidate).exists():
                    self.reference = candidate
                    break
        super().save(*args, **kwargs)


class SupportMessage(models.Model):
    request = models.ForeignKey(SupportRequest, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    from_staff = models.BooleanField(default=False)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
