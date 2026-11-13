"""Pre-made cards: batches, PINs and claiming.

A shop sells a printed card for GHS 450. The card carries a QR code and an NFC
chip that both open ``/c/<code>``, and a PIN on the back. Until someone claims
it, that link leads to a "set up your card" page. The buyer proves they hold the
card with the PIN, fills in their details and gets an account; the same link
then shows their card for good, and a year of the plan in the batch starts.

A *card only* batch (GHS 200) sells the printed card with no plan. Setting it
up gives the buyer an account and the card, still a draft; they then pay for
any plan at the normal checkout, which switches the card on and, when they pay
by card, starts automatic renewal.

A *pay when you update* batch (Basic or Professional only) sells the card with the plan's features
but no plan to pay for: setting it up puts it live at once and for good. Changes to its details, links and
web address are free for a year after the card is made; after that they cost the batch's Update Price, which
opens changes for 12 months (see ``apps.dashboard.updates``). The business page is always free.
"""

import csv
import hashlib
import hmac
import io
import secrets
import zipfile
from datetime import timedelta

import segno
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.core.models import AuditLog, Counter
from apps.core.utils import client_ip, rate_limited

from . import services
from .models import Card, CardPhone, CardSlug, PremadeBatch, PremadeCard
from django.utils.translation import gettext as _, ngettext

# No 0/O/1/I in codes people may read off a card and type by hand.
SLUG_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
PIN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SLUG_LENGTH = 8
PIN_LENGTH = 8
MAX_BATCH = 500
PAY_TO_UPDATE_PLANS = ("basic", "professional")  # plan codes a pay-when-you-update batch may carry
MAX_PIN_FAILURES = 5
LOCK_MINUTES = 60
PACK_TTL_SECONDS = 24 * 3600
CLAIM_WINDOW_SECONDS = 30 * 60  # how long a correct PIN stays good while the buyer fills in the form


class ClaimError(Exception):
    """Something the buyer can be told, in plain words."""


# --------------------------------------------------------------------------
# Codes and PINs
# --------------------------------------------------------------------------


def new_pin():
    return "".join(secrets.choice(PIN_ALPHABET) for _ in range(PIN_LENGTH))


def format_pin(pin):
    return f"{pin[:4]}-{pin[4:]}"


def normalise_pin(raw):
    return "".join(ch for ch in (raw or "").upper() if ch.isalnum())


def hash_pin(pin):
    """A keyed hash: a leaked database alone does not reveal or let anyone guess a PIN."""
    return hmac.new(settings.SECRET_KEY.encode(), f"premade-pin:{pin}".encode(), hashlib.sha256).hexdigest()


def _new_slugs(count):
    """Unique random card codes, checked against every link ever used."""
    chosen = set()
    while len(chosen) < count:
        candidates = {
            "".join(secrets.choice(SLUG_ALPHABET) for _ in range(SLUG_LENGTH)) for _ in range(count - len(chosen) + 20)
        }
        candidates = {c for c in candidates if not services.slug_problem(c)}
        taken = set(CardSlug.objects.filter(slug__in=candidates).values_list("slug", flat=True))
        taken |= set(Card.objects.filter(slug__in=candidates).values_list("slug", flat=True))
        for slug in candidates - taken:
            if len(chosen) < count:
                chosen.add(slug)
    return list(chosen)


# --------------------------------------------------------------------------
# Batches (staff)
# --------------------------------------------------------------------------


def pack_key(batch):
    return f"premade:pack:{batch.pk}"


def get_pack(batch):
    """The print pack (links and PINs), if it is still within its 24 hours."""
    return cache.get(pack_key(batch))


def _pack_row(premade, pin):
    return {
        "label": premade.label,
        "code": premade.card.slug,
        "qr_url": premade.card.public_url("qr"),
        "nfc_url": premade.card.public_url("nfc"),
        "pin": format_pin(pin),
    }


@transaction.atomic
def create_batch(
    staff, *, name, quantity, plan=None, price_minor=45000, months=12, note="", card_only=False,
    pay_to_update=False, update_price_minor=10000,
):
    if not 1 <= quantity <= MAX_BATCH:
        raise ValidationError(f"Make between 1 and {MAX_BATCH} cards at a time.")
    if pay_to_update:
        if plan is None or plan.code not in PAY_TO_UPDATE_PLANS:
            raise ValidationError("A pay-when-you-update card is for the Basic or Professional plan only.")
        months, card_only = 0, False
    elif card_only:
        plan, months = None, 0
    elif plan is None:
        raise ValidationError("Choose the plan that comes with the card.")
    code = f"B{Counter.next('premade-batch'):03d}"
    batch = PremadeBatch.objects.create(
        name=name.strip(), code=code, quantity=quantity, price_minor=price_minor, plan=plan, months=months,
        card_only=card_only, pay_to_update=pay_to_update, update_price_minor=update_price_minor,
        note=note.strip(), created_by=staff if getattr(staff, "pk", None) else None,
    )
    slugs = _new_slugs(quantity)
    cards = Card.objects.bulk_create(
        [Card(owner=None, full_name=f"Pre-made {code}-{i:04d}", slug=slug) for i, slug in enumerate(slugs, start=1)]
    )
    CardSlug.objects.bulk_create([CardSlug(slug=c.slug, card=c, is_current=True) for c in cards])
    pins = [new_pin() for _ in cards]
    premades = PremadeCard.objects.bulk_create(
        [PremadeCard(card=c, batch=batch, serial=i, pin_hash=hash_pin(p)) for i, (c, p) in enumerate(zip(cards, pins), start=1)]
    )
    rows = []
    for premade, card, pin in zip(premades, cards, pins):
        premade.card = card
        premade.batch = batch
        rows.append(_pack_row(premade, pin))
    cache.set(pack_key(batch), rows, PACK_TTL_SECONDS)
    AuditLog.record(
        staff, "premade_batch_created", batch, quantity=quantity, plan=plan.code if plan else "card only", price_minor=price_minor
    )
    return batch


@transaction.atomic
def reissue_pins(batch, staff):
    """New PINs for every card in the batch that is still unclaimed."""
    rows = []
    for premade in batch.cards.select_related("card", "batch").filter(claimed_at__isnull=True, voided_at__isnull=True):
        pin = new_pin()
        premade.pin_hash = hash_pin(pin)
        premade.pin_failures = 0
        premade.pin_locked_until = None
        premade.save(update_fields=["pin_hash", "pin_failures", "pin_locked_until"])
        rows.append(_pack_row(premade, pin))
    cache.set(pack_key(batch), rows, PACK_TTL_SECONDS)
    AuditLog.record(staff, "premade_pins_reissued", batch, cards=len(rows))
    return rows


@transaction.atomic
def void_card(premade, staff, reason=""):
    """Take an unclaimed card out of use (lost, damaged or misprinted)."""
    premade = PremadeCard.objects.select_for_update().select_related("card", "batch").get(pk=premade.pk)
    if premade.claimed_at:
        raise ValidationError(_("This card has already been set up by its buyer."))
    now = timezone.now()
    premade.voided_at = now
    premade.save(update_fields=["voided_at"])
    premade.card.deleted_at = now
    premade.card.save(update_fields=["deleted_at"])
    CardSlug.objects.filter(card=premade.card).update(retired_at=now)
    AuditLog.record(staff, "premade_card_voided", premade.card, reason=reason, label=premade.label)


def pack_csv(rows):
    out = io.StringIO()
    out.write("﻿")
    writer = csv.writer(out)
    writer.writerow(["Card", "Code", "QR code link", "NFC chip link", "PIN for the back"])
    for row in rows:
        writer.writerow([row["label"], row["code"], row["qr_url"], row["nfc_url"], row["pin"]])
    return out.getvalue().encode("utf-8")


def _qr_svg_file(url):
    out = io.BytesIO()
    segno.make(url, error="h").save(out, kind="svg", scale=10, dark="#000000", border=2, xmldecl=True, svgns=True)
    return out.getvalue()


def _qr_png_file(url):
    out = io.BytesIO()
    segno.make(url, error="h").save(out, kind="png", scale=14, dark="#000000", light="#ffffff", border=2)
    return out.getvalue()


def pack_zip(rows):
    """Every QR code as a vector SVG and a PNG, named by card number, plus the list."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("cards.csv", pack_csv(rows))
        for row in rows:
            archive.writestr(f"svg/{row['label']}-{row['code']}.svg", _qr_svg_file(row["qr_url"]))
            archive.writestr(f"png/{row['label']}-{row['code']}.png", _qr_png_file(row["qr_url"]))
    return buffer.getvalue()


# --------------------------------------------------------------------------
# Buyers: PIN check and claiming
# --------------------------------------------------------------------------


def check_pin(premade, raw_pin, request):
    """Return True for the right PIN, else raise ClaimError. Guesses are limited per card and per connection."""
    if premade.claimed_at:
        raise ClaimError(_("This card has already been set up."))
    if premade.voided_at:
        raise ClaimError(_("This card is no longer in use. Please contact the shop you bought it from."))
    if rate_limited(f"premade-pin-ip:{client_ip(request)}", 30, 3600):
        raise ClaimError(_("Too many attempts from this connection. Please try again later."))
    now = timezone.now()
    failed_message = None
    with transaction.atomic():
        locked = PremadeCard.objects.select_for_update().get(pk=premade.pk)
        if locked.pin_locked_until and locked.pin_locked_until > now:
            minutes = max(1, int((locked.pin_locked_until - now).total_seconds() // 60) + 1)
            raise ClaimError(ngettext(
                "Too many wrong PINs. Please try again in %(minutes)d minute.",
                "Too many wrong PINs. Please try again in %(minutes)d minutes.", minutes,
            ) % {"minutes": minutes})
        pin = normalise_pin(raw_pin)
        if len(pin) == PIN_LENGTH and hmac.compare_digest(hash_pin(pin), locked.pin_hash):
            if locked.pin_failures:
                PremadeCard.objects.filter(pk=locked.pk).update(pin_failures=0, pin_locked_until=None)
            return True
        failures = locked.pin_failures + 1
        if failures >= MAX_PIN_FAILURES:
            PremadeCard.objects.filter(pk=locked.pk).update(
                pin_failures=0, pin_locked_until=now + timedelta(minutes=LOCK_MINUTES)
            )
            failed_message = _("That PIN is not right. This card is locked for %(minutes)d minutes.") % {"minutes": LOCK_MINUTES}
        else:
            PremadeCard.objects.filter(pk=locked.pk).update(pin_failures=F("pin_failures") + 1)
            left = MAX_PIN_FAILURES - failures
            failed_message = ngettext(
                "That PIN is not right. %(left)d try left.", "That PIN is not right. %(left)d tries left.", left
            ) % {"left": left}
    raise ClaimError(failed_message)


def _live_places(user):
    # A pay-when-you-update card lives on its own terms and uses no place on the plan.
    return user.cards.filter(deleted_at__isnull=True, is_enabled=True, update_plan__isnull=True).count()


def _running(subscription, now):
    return bool(subscription and not subscription.cancelled_at and subscription.expires_at > now)


def attach_problem(user, premade):
    """Why this account cannot take the card, or None when it can."""
    if user.is_suspended or not user.is_active:
        return "This account cannot add a card at the moment. Please contact support."
    plan = premade.batch.plan
    subscription = user.subscription
    if premade.batch.pay_to_update:
        return None  # live on its own terms: no plan to start and no place to take
    if premade.batch.card_only:
        if not _running(subscription, timezone.now()):
            return None  # nothing is switched on yet, so there is no limit to hit
        limit = subscription.card_limit
    elif _running(subscription, timezone.now()) and subscription.yearly_price >= plan.price_minor:
        limit = subscription.card_limit
    else:
        limit = plan.team_size() if plan.is_team else plan.max_cards
    if _live_places(user) >= limit:
        return (
            "Your plan already uses all of its live card places. Switch a card off in your dashboard first, "
            "or set this card up with a different email address."
        )
    return None


def _receipt_number():
    day = timezone.localdate()
    return f"ADR-{day:%Y}-{Counter.next(f'receipt-{day:%Y}'):05d}"


def _finish(premade, user):
    """Record the sale as revenue, start the year and hand the card over. Runs inside the caller's transaction.

    A card-only sale starts no plan: the card goes live at once only if the buyer's own plan is running
    and has room; otherwise it stays a draft until they pay for a plan.
    """
    from apps.billing import fx, paystack
    from apps.billing.models import Payment
    from apps.billing.services import activate, activate_cards, send_receipt

    batch, plan, now = premade.batch, premade.batch.plan, timezone.now()
    if batch.pay_to_update:
        what = f"{batch.name} pre-made card ({plan.name} features, pay when you update)"
    elif batch.card_only:
        what = f"{batch.name} pre-made card (card only)"
    else:
        what = f"{batch.name} pre-made card with {plan.name}, {batch.months} months"
    rate = fx.latest_rate()
    payment = Payment.objects.create(
        user=user,
        reference=paystack.new_reference("SHOP"),
        purpose=Payment.PURPOSE_PREMADE,
        plan=plan,
        months=batch.months,
        subtotal_minor=batch.price_minor,
        amount_minor=batch.price_minor,
        status=Payment.SUCCESS,
        gateway="shop",
        channel="shop sale",
        paid_at=now,
        fx_rate=rate.ghs_per_usd if rate else None,
        fx_rate_at=rate.fetched_at if rate else None,
        receipt_number=_receipt_number(),
        manual_reason=f"Pre-made card {premade.label} ({batch.name}), sold in a shop and set up by its buyer.",
        line_items=[{"label": what, "amount_minor": batch.price_minor}],
        gateway_response={"premade_card": premade.label, "batch": batch.code},
    )
    subscription = user.subscription
    if batch.pay_to_update:
        # Live at once on the plan's features, with no subscription; changes are paid for when made.
        card = premade.card
        card.update_plan = plan
        card.update_price_minor = batch.update_price_minor
        card.activated_at = now
        card.is_enabled = True
        card.save(update_fields=["update_plan", "update_price_minor", "activated_at", "is_enabled", "updated_at"])
    elif batch.card_only:
        if _running(subscription, now):
            activate_cards(user, subscription)
    elif _running(subscription, now) and subscription.yearly_price >= plan.price_minor:
        # Already on an equal or better plan: add the year and keep the plan.
        subscription.extend(batch.months)
        subscription.save()
        activate_cards(user, subscription)
    else:
        activate(user, plan, batch.months)
    premade.claimed_at = now
    premade.claimed_by = user
    premade.payment = payment
    premade.save(update_fields=["claimed_at", "claimed_by", "payment"])
    AuditLog.record(user, "premade_card_claimed", premade.card, label=premade.label, amount_minor=payment.amount_minor)
    transaction.on_commit(lambda: send_receipt(payment))
    return payment


def _lock_claimable(premade_pk):
    premade = PremadeCard.objects.select_for_update().select_related("card", "batch", "batch__plan").get(pk=premade_pk)
    if premade.claimed_at:
        raise ClaimError(_("This card has already been set up."))
    if premade.voided_at:
        raise ClaimError(_("This card is no longer in use. Please contact the shop you bought it from."))
    return premade


@transaction.atomic
def claim_with_new_account(premade_pk, data):
    from apps.accounts.models import User

    premade = _lock_claimable(premade_pk)
    user = User.objects.create_user(
        email=data["email"], password=data["password"], full_name=data["full_name"].strip(),
        phone=data["phone"], accepted_terms_at=timezone.now(),
    )
    card = premade.card
    card.owner = user
    card.full_name = data["full_name"].strip()
    card.email = data["email"]
    card.job_title = data.get("job_title", "")
    card.business_name = data.get("business_name", "")
    card.save(update_fields=["owner", "full_name", "email", "job_title", "business_name", "updated_at"])
    CardPhone.objects.create(card=card, number=data["phone"], label="", position=0)
    payment = _finish(premade, user)
    return user, card, payment


@transaction.atomic
def claim_for_existing_account(premade_pk, user):
    premade = _lock_claimable(premade_pk)
    problem = attach_problem(user, premade)
    if problem:
        raise ClaimError(problem)
    card = premade.card
    card.owner = user
    card.full_name = user.full_name or user.display_name
    card.email = user.email
    card.save(update_fields=["owner", "full_name", "email", "updated_at"])
    if user.phone and not card.phones.exists():
        CardPhone.objects.create(card=card, number=user.phone, label="", position=0)
    payment = _finish(premade, user)
    return card, payment
