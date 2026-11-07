"""Pay-when-you-update cards: hold the editor's Save while the owner pays, then apply it.

A card from a "pay when you update" pre-made batch goes live free. Changing what the public sees
needs the Update Price, which unlocks changes for 12 months. In the card editor the owner edits
first and pays at Save: the valid form is kept (``CardUpdate``, pictures included, already in
storage) and applied by ``unlock_and_apply`` when the payment clears. A held Save nobody pays for
is thrown away after ``HOLD_HOURS``.
"""

import logging
import os
import uuid
from datetime import timedelta

from dateutil.relativedelta import relativedelta
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.forms import inlineformset_factory
from django.utils import timezone
from django.utils.datastructures import MultiValueDict

from apps.billing import paystack
from apps.billing.models import Payment
from apps.billing.services import card_update_quote, create_payment, plan_for_card, verify_and_fulfil
from apps.cards.models import Card, CardPhone, CardUpdate
from apps.core.models import AuditLog

from .forms import CardForm, PhoneForm

logger = logging.getLogger(__name__)

HOLD_HOURS = 48
UNLOCK_MONTHS = 12
SAVED_CARD = "saved-card"  # Payment.channel while a kept-card charge is still pending


def editor_forms(card, data=None, files=None):
    """The card editor's form and phone numbers, for a post (or None for a blank GET)."""
    plan = plan_for_card(card)
    Phones = inlineformset_factory(
        Card, CardPhone, form=PhoneForm, extra=1 if card.phones.count() < plan.max_phones else 0,
        max_num=plan.max_phones, validate_max=True, can_delete=True,
    )
    return CardForm(data, files, instance=card, plan=plan), Phones(data, instance=card, prefix="phones")


def save_editor(card, form, phones):
    form.save()
    phones.save()
    for position, phone in enumerate(card.phones.all()):
        if phone.position != position:
            CardPhone.objects.filter(pk=phone.pk).update(position=position)


def _delete(held):
    for info in (held.files or {}).values():
        try:
            default_storage.delete(info["path"])
        except Exception:  # a picture that cannot be deleted must not stop the rest
            logger.warning("Could not delete held picture %s", info.get("path"))
    held.delete()


def discard(card):
    held = CardUpdate.objects.filter(card=card).first()
    if held:
        _delete(held)


def hold(card, post, files):
    """Keep a Save that already passed the form until the owner pays. Replaces an earlier held Save."""
    discard(card)
    data = {key: post.getlist(key) for key in post if key != "csrfmiddlewaretoken"}
    stored = {}
    for field, upload in files.items():
        upload.seek(0)
        extension = os.path.splitext(upload.name)[1].lower()
        path = default_storage.save(f"held/{card.pk}/{uuid.uuid4().hex}{extension}", upload)
        stored[field] = {"path": path, "name": upload.name, "content_type": upload.content_type or ""}
    return CardUpdate.objects.create(card=card, data=data, files=stored)


def _replay(card, held):
    """Run the held post through the same forms the editor uses. False when it no longer passes."""
    files = MultiValueDict()
    for field, info in (held.files or {}).items():
        with default_storage.open(info["path"]) as handle:
            content = handle.read()
        files[field] = SimpleUploadedFile(info["name"], content, info["content_type"] or "application/octet-stream")
    form, phones = editor_forms(card, MultiValueDict(held.data), files)
    if not (form.is_valid() and phones.is_valid()):
        return False
    save_editor(card, form, phones)
    return True


def unlock_and_apply(payment):
    """A card-update payment cleared: unlock the card for 12 months and apply what was waiting.

    Runs inside ``fulfil``'s transaction. Applying is best effort: the payment stands either way.
    """
    if not payment.card_id:
        return
    card = Card.objects.select_for_update().get(pk=payment.card_id)
    now = timezone.now()
    start = card.updates_paid_until if card.updates_paid_until and card.updates_paid_until > now else now
    card.updates_paid_until = start + relativedelta(months=payment.months or UNLOCK_MONTHS)
    card.save(update_fields=["updates_paid_until", "updated_at"])
    held = CardUpdate.objects.filter(card=card).first()
    if held is None:
        return
    try:
        with transaction.atomic():
            applied = _replay(card, held)
    except Exception:
        logger.exception("Applying held changes to card %s failed", card.pk)
        applied = False
    AuditLog.record(card.owner, "card_update_applied" if applied else "card_update_failed", card, reference=payment.reference)
    _delete(held)


def charge_kept_card(card):
    """The owner confirmed: charge the Update Price to the card kept at their last payment.

    Returns the Payment. SUCCESS means the year is open and held changes are applied; PENDING means
    Paystack has not settled it yet (the webhook finishes the job, and a second click finds this payment
    instead of charging again). The kept card stays after a decline, so one bad day loses nothing.
    """
    now = timezone.now()
    with transaction.atomic():
        Card.objects.select_for_update().get(pk=card.pk)  # two quick clicks must not charge twice
        recent = Payment.objects.filter(
            card=card, purpose=Payment.PURPOSE_CARD_UPDATE, channel=SAVED_CARD, status=Payment.PENDING,
            created_at__gte=now - timedelta(minutes=2),
        ).first()
        if recent:
            return recent
        payment = create_payment(
            user=card.owner, purpose=Payment.PURPOSE_CARD_UPDATE, quote=card_update_quote(card),
            months=UNLOCK_MONTHS, card=card,
        )
        Payment.objects.filter(pk=payment.pk).update(channel=SAVED_CARD)
    try:
        paystack.charge_authorization(
            email=card.update_authorization_email or card.owner.email, amount_minor=payment.amount_minor,
            reference=payment.reference, authorization_code=card.update_authorization_code,
            metadata={"payment_id": payment.pk, "purpose": payment.purpose, "card_id": card.pk},
        )
    except paystack.PaymentError as exc:
        logger.warning("Kept-card charge %s was declined: %s", payment.reference, exc)
        Payment.objects.filter(pk=payment.pk, status=Payment.PENDING).update(
            status=Payment.FAILED, gateway_response={"error": str(exc)[:200]}
        )
    else:
        verify_and_fulfil(payment, "kept card")
    payment.refresh_from_db()
    return payment


def discard_stale(hours=HOLD_HOURS):
    """Daily job: throw away held Saves nobody paid for, and their pictures."""
    stale = list(CardUpdate.objects.filter(created_at__lt=timezone.now() - timedelta(hours=hours)))
    for held in stale:
        _delete(held)
    return len(stale)
