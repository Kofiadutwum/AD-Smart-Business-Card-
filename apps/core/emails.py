"""Transactional email (Section 16).

Every email is rendered from ``templates/emails/<name>.txt`` (plain text,
always) and wrapped in the branded HTML layout. Each send is written to
EmailLog. A ``dedupe_key`` turns a send into a once-only send, which is what
keeps the daily reminder job safe to re-run.
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

    log = EmailLog(
        user=user,
        kind=kind,
        to=to,
        subject=subject[:255],
        body=body,
        dedupe_key=dedupe_key,
        sent_by=sent_by,
    )
    try:
        with transaction.atomic():
            log.save()
    except IntegrityError:
        return False  # another worker claimed this dedupe key first

    message = EmailMultiAlternatives(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to],
        reply_to=[reply_to or SiteSettings.load().support_email],
    )
    message.attach_alternative(html, "text/html")
    for filename, content, mimetype in attachments or []:
        message.attach(filename, content, mimetype)

    try:
        message.send()
    except Exception as exc:  # provider errors vary; never break the caller
        logger.exception("Email %s to %s failed", kind, to)
        log.status = EmailLog.STATUS_FAILED
        log.error = str(exc)[:2000]
        # Free the dedupe key so a later run can try again.
        log.dedupe_key = None
        log.save(update_fields=["status", "error", "dedupe_key"])
        return False
    return True


def notify_admins(subject, template, context=None, kind=None, dedupe_key=None):
    return send_email(
        to=SiteSettings.load().admin_notification_email,
        subject=subject,
        template=template,
        context=context,
        kind=kind or f"admin:{template}",
        dedupe_key=dedupe_key,
    )
