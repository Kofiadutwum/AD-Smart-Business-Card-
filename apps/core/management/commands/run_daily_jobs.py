"""The daily job (Render cron). Every step is idempotent and safe to re-run.

    python manage.py run_daily_jobs
    python manage.py run_daily_jobs --only reminders
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.services import anonymise_user
from apps.billing import fx
from apps.billing.models import Subscription
from apps.billing.services import activate_cards, expire_stale_pending
from apps.cards.models import CardEvent
from apps.core.emails import retry_failed_emails, send_email
from apps.core.models import SiteSettings
from apps.nfc import services as nfc
from apps.nfc.models import NFCOrder

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Reminders, expiry notices, NFC follow-ups, retention and payment housekeeping."

    def add_arguments(self, parser):
        parser.add_argument("--only", help="Run a single step by name.")

    def handle(self, *args, **options):
        steps = [
            ("exchange_rate", self.exchange_rate),
            ("pending_plans", self.pending_plans),
            ("reminders", self.reminders),
            ("expiry_notices", self.expiry_notices),
            ("retention", self.retention),
            ("deletions", self.deletions),
            ("nfc_followups", self.nfc_followups),
            ("payments", self.payments),
            ("email_retries", self.email_retries),
            ("analytics_retention", self.analytics_retention),
        ]
        for name, step in steps:
            if options.get("only") and options["only"] != name:
                continue
            try:
                result = step()
                self.stdout.write(f"{name}: {result}")
            except Exception:  # one failing step must not stop the others
                logger.exception("Daily job step %s failed", name)
                self.stderr.write(f"{name}: FAILED (see logs)")

    # ------------------------------------------------------------------

    def exchange_rate(self):
        rate = fx.refresh_rate()
        return str(rate) if rate else "provider unavailable; keeping last rate"

    def pending_plans(self):
        changed = 0
        for sub in Subscription.objects.filter(pending_plan__isnull=False, pending_plan_from__lte=timezone.now()):
            if sub.apply_pending_plan():
                sub.save()
                activate_cards(sub.user, sub)
                changed += 1
        return f"{changed} plan change(s) applied"

    def reminders(self):
        """EXP-01: 30, 7 and 1 day before, and on the expiry date."""
        site = SiteSettings.load()
        thresholds = sorted(site.reminder_day_list)  # e.g. [0, 1, 7, 30]
        today = timezone.localdate()
        sent = 0
        subs = Subscription.objects.select_related("user", "plan").filter(
            cancelled_at__isnull=True,
            expires_at__date__gte=today,
            expires_at__date__lte=today + timedelta(days=max(thresholds or [0])),
            user__is_active=True,
        )
        for sub in subs:
            days = (timezone.localtime(sub.expires_at).date() - today).days
            due = next((t for t in thresholds if days <= t), None)
            if due is None:
                continue
            if send_email(
                to=sub.user.email,
                subject="Your card plan ends today" if days == 0 else f"Your card plan ends in {days} day{'s' if days != 1 else ''}",
                template="renewal_reminder",
                context={"user": sub.user, "subscription": sub, "days": days},
                user=sub.user,
                kind=f"renewal_{due}",
                dedupe_key=f"renewal:{sub.pk}:{sub.expires_at:%Y%m%d}:{due}",
            ):
                sent += 1
        return f"{sent} reminder(s)"

    def expiry_notices(self):
        """Grace-period start and card-inactive notices, once per expiry."""
        now = timezone.now()
        site = SiteSettings.load()
        grace = timedelta(days=site.grace_period_days)
        sent = 0
        for sub in Subscription.objects.select_related("user", "plan").filter(
            cancelled_at__isnull=True, expires_at__lt=now, expires_at__gte=now - grace - timedelta(days=7)
        ):
            state = sub.state
            if state == Subscription.STATE_GRACE:
                key, template, subject = "grace", "grace_started", "Your card is in its grace period"
            elif state == Subscription.STATE_INACTIVE and now - sub.grace_ends_at < timedelta(days=7):
                key, template, subject = "inactive", "card_inactive", "Your card is now inactive"
            else:
                continue
            if send_email(
                to=sub.user.email, subject=subject, template=template,
                context={"user": sub.user, "subscription": sub}, user=sub.user,
                kind=template, dedupe_key=f"{key}:{sub.pk}:{sub.expires_at:%Y%m%d}",
            ):
                sent += 1
        return f"{sent} notice(s)"

    def retention(self):
        """EXP-06: warn 30 days before deleting data of long-inactive cards.

        Deletion itself only runs when the Main Admin has switched on
        'auto purge' in settings.
        """
        site = SiteSettings.load()
        now = timezone.now()
        keep = timedelta(days=30 * site.data_retention_months)
        grace = timedelta(days=site.grace_period_days)
        warned = purged = 0
        for sub in Subscription.objects.select_related("user").filter(cancelled_at__isnull=True, user__anonymised_at__isnull=True):
            inactive_since = sub.expires_at + grace
            if now < inactive_since:
                continue
            delete_on = inactive_since + keep
            if now >= delete_on - timedelta(days=30) and now < delete_on:
                if send_email(
                    to=sub.user.email, subject="Your card details will be deleted soon", template="data_deletion_warning",
                    context={"user": sub.user, "delete_on": delete_on}, user=sub.user,
                    kind="data_deletion_warning", dedupe_key=f"purge-warn:{sub.pk}:{sub.expires_at:%Y%m%d}",
                ):
                    warned += 1
            elif now >= delete_on and site.auto_purge_enabled:
                anonymise_user(sub.user, reason="Retention period ended")
                purged += 1
        return f"{warned} warning(s), {purged} purged"

    def deletions(self):
        """PRV-04: remove personal data 30 days after a deletion request."""
        from apps.accounts.models import User

        cutoff = timezone.now() - timedelta(days=30)
        done = 0
        for user in User.objects.filter(deletion_requested_at__lte=cutoff, anonymised_at__isnull=True, is_staff=False):
            anonymise_user(user, reason="Customer requested deletion")
            done += 1
        return f"{done} account(s) anonymised"

    def nfc_followups(self):
        reminded, held = nfc.run_proof_reminders()
        expired = nfc.expire_unpaid_orders()
        retried = 0
        for order in NFCOrder.objects.filter(paid_at__isnull=False, admin_notified_at__isnull=True):
            if nfc.notify_admin_new_order(order):
                retried += 1
        return f"{reminded} proof reminder(s), {held} put on hold, {expired} unpaid cancelled, {retried} admin alert(s) resent"

    def payments(self):
        return f"{expire_stale_pending()} stale payment(s) settled"

    def email_retries(self):
        """Re-send emails that failed in the last 3 days (e.g. the provider's daily
        limit was reached): receipts, order updates, lead and support emails."""
        sent, failing = retry_failed_emails()
        return f"{sent} failed email(s) re-sent, {failing} still failing"

    def analytics_retention(self):
        """ANL-04: keep analytics for 24 months."""
        cutoff = timezone.now() - timedelta(days=730)
        deleted, _ = CardEvent.objects.filter(occurred_at__lt=cutoff).delete()
        return f"{deleted} old event(s) removed"
