"""Platform-wide records: editable settings, the audit log and the email log."""

from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.db import models, transaction


class SiteSettings(models.Model):
    """Business settings the Main Admin can change without a developer (ADM-04).

    Stored as a single row. Plan prices, NFC price tiers and delivery zones
    live in their own tables because they are lists, not single values.
    """

    CACHE_KEY = "site-settings"

    grace_period_days = models.PositiveSmallIntegerField(default=14)
    reminder_days = models.CharField(
        max_length=64,
        default="30,7,1,0",
        help_text="Days before expiry to send renewal reminders; 0 means on the expiry date.",
    )
    data_retention_months = models.PositiveSmallIntegerField(default=12)
    auto_purge_enabled = models.BooleanField(
        default=False,
        help_text="Delete personal data of cards inactive longer than the retention period. "
        "Leave off until the business has confirmed the retention policy.",
    )
    free_revision_rounds = models.PositiveSmallIntegerField(default=2)
    proof_reminder_days = models.CharField(max_length=32, default="3,7")
    on_hold_after_days = models.PositiveSmallIntegerField(default=14)
    tax_rate_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    tax_label = models.CharField(max_length=64, default="Tax", blank=True)
    fx_fallback_hours = models.PositiveSmallIntegerField(default=72)
    leads_enabled = models.BooleanField(default=True)

    support_phone = models.CharField(max_length=32, default="+233257932232")
    support_whatsapp = models.CharField(max_length=32, default="+233545875881")
    support_email = models.EmailField(default="adgraphics881@gmail.com")
    instagram_handle = models.CharField(max_length=64, default="adgraphics__")
    admin_notification_email = models.EmailField(default="adgraphics881@gmail.com")
    office_address = models.CharField(max_length=200, default="Cape Coast, Ghana", blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "site settings"
        verbose_name_plural = "site settings"

    def __str__(self):
        return "Site settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete(self.CACHE_KEY)

    @classmethod
    def load(cls):
        value = cache.get(cls.CACHE_KEY)
        if value is None:
            value, _ = cls.objects.get_or_create(pk=1)
            cache.set(cls.CACHE_KEY, value, 300)
        return value

    @staticmethod
    def _int_list(raw):
        out = []
        for part in (raw or "").split(","):
            part = part.strip()
            if part.isdigit():
                out.append(int(part))
        return sorted(set(out), reverse=True)

    @property
    def reminder_day_list(self):
        return self._int_list(self.reminder_days)

    @property
    def proof_reminder_day_list(self):
        return sorted(self._int_list(self.proof_reminder_days))

    @property
    def whatsapp_link(self):
        digits = "".join(ch for ch in self.support_whatsapp if ch.isdigit())
        return f"https://wa.me/{digits}"

    @property
    def instagram_link(self):
        return f"https://instagram.com/{self.instagram_handle.lstrip('@')}"


class AuditLog(models.Model):
    """Who did what, when and why (Section 14, SEC-10)."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
    )
    action = models.CharField(max_length=64, db_index=True)
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    target_label = models.CharField(max_length=255, blank=True)
    reason = models.TextField(blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.target_label}"

    @classmethod
    def record(cls, actor, action, target=None, reason="", **details):
        entry = cls(
            actor=actor if getattr(actor, "pk", None) else None,
            action=action,
            reason=reason or "",
            details=details,
        )
        if target is not None:
            entry.target_type = target.__class__.__name__
            entry.target_id = str(getattr(target, "pk", ""))
            entry.target_label = str(target)[:255]
        entry.save()
        return entry


class EmailLog(models.Model):
    """One row per email the platform tried to send.

    ``dedupe_key`` makes scheduled emails idempotent: the daily job can run
    twice, or be re-run after a crash, without anyone receiving a second
    reminder.
    """

    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    kind = models.CharField(max_length=64, db_index=True)
    to = models.CharField(max_length=255)
    subject = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    status = models.CharField(max_length=16, default=STATUS_SENT)
    error = models.TextField(blank=True)
    dedupe_key = models.CharField(max_length=160, unique=True, null=True, blank=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="emails_sent_as_staff",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.kind} -> {self.to} ({self.status})"


class Counter(models.Model):
    """Gap-free sequences, e.g. receipt numbers (PAY-08)."""

    name = models.CharField(max_length=64, unique=True)
    value = models.PositiveIntegerField(default=0)

    @classmethod
    def next(cls, name):
        with transaction.atomic():
            counter, _ = cls.objects.select_for_update().get_or_create(name=name)
            counter.value += 1
            counter.save(update_fields=["value"])
            return counter.value
