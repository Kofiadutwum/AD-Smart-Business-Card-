"""Transactional email (Section 16).

Every email is rendered from ``templates/emails/<name>.txt`` (plain text,
always) and wrapped in the branded HTML layout. Each send is written to
EmailLog. A ``dedupe_key`` turns a send into a once-only send, which is what
keeps the daily reminder job safe to re-run. Sends the provider refuses (for
example over Resend's free daily limit) are retried by the daily job for three
days; see ``retry_failed_emails``.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string

from .models import EmailLog, SiteSettings

logger = logging.getLogger(__name__)


def _context(extra):
    ctx = {
        "site_name": settings.SITE_NAME,
        "site_url": settings.SITE_URL,
        "site": SiteSettings.load(),
    }
    ctx.update(extra or {})
    return ctx


def send_email(
    *,
    to,
    subject,
    template,
    context=None,
    kind=None,
    user=None,
    dedupe_key=None,
    attachments=None,
    sent_by=None,
    reply_to=None,
):
    """Render and send one email. Returns True when it was handed to the provider.

    Never raises for delivery problems: a failed email must not undo a payment
    or an order update. Failures are logged with the error for retrying.
    """
    kind = kind or template
    if dedupe_key and EmailLog.objects.filter(dedupe_key=dedupe_key).exists():
        return False

    ctx = _context(context)
    ctx["subject"] = subject
    body = render_to_string(f"emails/{template}.txt", ctx).strip() + "\n"
    ctx["body_text"] = body
    html = render_to_string("emails/_layout.html", ctx)

    reply_to = reply_to or SiteSettings.load().support_email
    log = EmailLog(
        user=user,
        kind=kind,
        to=to,
        subject=subject[:255],
        body=body,
        dedupe_key=dedupe_key,
        reply_to=reply_to,
        sent_by=sent_by,
    )
    try:
        with transaction.atomic():
            log.save()
    except IntegrityError:
        return False  # another worker claimed this dedupe key first

    try:
        _build(subject, body, html, to, reply_to, attachments).send()
    except Exception as exc:  # provider errors vary; never break the caller
        logger.exception("Email %s to %s failed", kind, to)
        log.status = EmailLog.STATUS_FAILED
        log.error = str(exc)[:2000]
        # Free the dedupe key so a later run can try again.
        log.failed_key = dedupe_key or ""
        log.dedupe_key = None
        log.save(update_fields=["status", "error", "dedupe_key", "failed_key"])
        return False
    return True


def _build(subject, body, html, to, reply_to, attachments):
    message = EmailMultiAlternatives(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to],
        reply_to=[reply_to],
    )
    message.attach_alternative(html, "text/html")
    for filename, content, mimetype in attachments or []:
        message.attach(filename, content, mimetype)
    return message


# Emails the daily job already re-sends through their own step (reminders,
# expiry notices, proof reminders, new-order alerts); retrying them here too
# would send two copies.
RETRIED_ELSEWHERE = (
    "renewal_", "grace_started", "card_inactive", "data_deletion_warning",
    "nfc:nfc_proof_reminder", "admin:admin_nfc_order",
)
# Codes and links that are useless a day later; the customer asks again.
TIME_SENSITIVE = ("verify_email", "password_reset_code")


def retry_failed_emails(days=3, max_retries=3):
    """Re-send emails that failed recently, for example when the provider's
    daily limit was reached. Returns (sent, still_failing)."""
    from datetime import timedelta

    from django.utils import timezone

    sent = failing = 0
    since = timezone.now() - timedelta(days=days)
    rows = EmailLog.objects.filter(
        status=EmailLog.STATUS_FAILED, created_at__gte=since, retries__lt=max_retries
    ).order_by("created_at")
    for log in rows:
        if log.kind.startswith(RETRIED_ELSEWHERE) or log.kind in TIME_SENSITIVE:
            continue
        if log.failed_key and EmailLog.objects.filter(dedupe_key=log.failed_key).exists():
            # Delivered since by another path: nothing to do.
            EmailLog.objects.filter(pk=log.pk).update(retries=max_retries)
            continue
        attachments, payment = _attachments_for(log)
        html = render_to_string(
            "emails/_layout.html", _context({"subject": log.subject, "body_text": log.body})
        )
        log.retries += 1
        try:
            _build(log.subject, log.body, html, log.to,
                   log.reply_to or SiteSettings.load().support_email, attachments).send()
        except Exception as exc:
            log.error = str(exc)[:2000]
            log.save(update_fields=["retries", "error"])
            failing += 1
            continue
        log.status = EmailLog.STATUS_SENT
        log.error = ""
        log.dedupe_key = log.failed_key or None
        try:
            with transaction.atomic():
                log.save(update_fields=["retries", "error", "status", "dedupe_key"])
        except IntegrityError:
            log.dedupe_key = None
            log.save(update_fields=["retries", "error", "status", "dedupe_key"])
        if payment is not None:
            payment.__class__.objects.filter(pk=payment.pk).update(receipt_emailed_at=timezone.now())
        sent += 1
    return sent, failing


def _attachments_for(log):
    """Rebuild attachments that are not stored: today only the receipt PDF."""
    if log.kind == "payment_receipt" and log.failed_key.startswith("receipt:"):
        from apps.billing.models import Payment
        from apps.billing.receipts import render_receipt_pdf

        payment = Payment.objects.filter(reference=log.failed_key.removeprefix("receipt:")).first()
        if payment:
            return [(f"{payment.receipt_number}.pdf", render_receipt_pdf(payment), "application/pdf")], payment
    return [], None


def notify_admins(subject, template, context=None, kind=None, dedupe_key=None):
    return send_email(
        to=SiteSettings.load().admin_notification_email,
        subject=subject,
        template=template,
        context=context,
        kind=kind or f"admin:{template}",
        dedupe_key=dedupe_key,
    )
