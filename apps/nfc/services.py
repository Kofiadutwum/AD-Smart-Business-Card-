"""NFC order pricing and workflow (Sections 7 and 8)."""

import secrets
from datetime import timedelta

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.emails import notify_admins, send_email
from apps.core.models import AuditLog, SiteSettings

from .models import DesignProof, NFCOrder, OrderEvent, PriceTier

# --------------------------------------------------------------------------
# Pricing (7.1, NFP-01, NFP-02)
# --------------------------------------------------------------------------


def tiers():
    return list(PriceTier.objects.order_by("min_quantity"))


def cards_price(quantity, tier_list=None):
    """Total card price in pesewas for ``quantity`` cards."""
    if quantity < 1:
        return 0
    tier_list = tier_list if tier_list is not None else tiers()
    for index, tier in enumerate(tier_list):
        top = tier.max_quantity
        if quantity >= tier.min_quantity and (top is None or quantity <= top):
            if tier.mode == PriceTier.INCREMENTAL and index > 0:
                base_quantity = tier.min_quantity - 1
                return cards_price(base_quantity, tier_list[:index]) + tier.unit_price_minor * (
                    quantity - base_quantity
                )
            return tier.unit_price_minor * quantity
    raise ValueError("No NFC price is set for that quantity.")


def free_extra_hint(quantity, tier_list=None):
    """NFP-02: 'Add 1 more card for no extra cost' when the next one is free."""
    tier_list = tier_list if tier_list is not None else tiers()
    try:
        return cards_price(quantity + 1, tier_list) <= cards_price(quantity, tier_list)
    except ValueError:
        return False


def pricing_problems(tier_list=None, up_to=100):
    """Quantities where a larger order would cost less than a smaller one."""
    tier_list = tier_list if tier_list is not None else tiers()
    problems = []
    previous = 0
    for quantity in range(1, up_to + 1):
        try:
            price = cards_price(quantity, tier_list)
        except ValueError:
            problems.append((quantity, "no price"))
            continue
        if price < previous:
            problems.append((quantity, "cheaper than %d" % (quantity - 1)))
        previous = price
    return problems


def tiers_json(tier_list=None):
    tier_list = tier_list if tier_list is not None else tiers()
    return [
        {"min": t.min_quantity, "max": t.max_quantity, "unit": t.unit_price_minor, "mode": t.mode}
        for t in tier_list
    ]


# --------------------------------------------------------------------------
# Orders
# --------------------------------------------------------------------------


def new_order_number():
    while True:
        number = f"NFC-{secrets.token_hex(3).upper()}"
        if not NFCOrder.objects.filter(order_number=number).exists():
            return number


def log(order, message="", status="", actor=None, visible=True):
    return OrderEvent.objects.create(
        order=order, message=message, status=status, actor=actor, visible_to_customer=visible
    )


def _order_link(order):
    from django.conf import settings

    return settings.SITE_URL + reverse("nfc:detail", args=[order.order_number])


def email_customer(order, subject, template, extra=None, dedupe_key=None):
    context = {"order": order, "order_url": _order_link(order)}
    context.update(extra or {})
    return send_email(
        to=order.user.email,
        subject=subject,
        template=template,
        context=context,
        user=order.user,
        kind=f"nfc:{template}",
        dedupe_key=dedupe_key,
    )


def mark_paid(order, payment):
    """Called by billing.fulfil inside its transaction (Figure 4C)."""
    order = NFCOrder.objects.select_for_update().get(pk=order.pk)
    if order.paid_at:
        return order
    order.paid_at = payment.paid_at or timezone.now()
    order.status = NFCOrder.PAYMENT_CONFIRMED
    order.save(update_fields=["paid_at", "status", "updated_at"])
    log(order, "Payment verified with Paystack.", NFCOrder.PAYMENT_CONFIRMED)
    transaction.on_commit(lambda: _after_paid(order.pk))
    return order


def _after_paid(order_pk):
    order = NFCOrder.objects.get(pk=order_pk)
    email_customer(
        order,
        f"Order {order.order_number} confirmed",
        "nfc_order_confirmed",
        dedupe_key=f"nfc-confirmed:{order.pk}",
    )
    notify_admin_new_order(order)


def notify_admin_new_order(order):
    """Retried by the daily job until it succeeds."""
    if order.admin_notified_at:
        return True
    from django.conf import settings

    sent = notify_admins(
        f"New NFC order paid — {order.order_number}",
        "admin_nfc_order",
        {"order": order, "staff_url": settings.SITE_URL + reverse("staff:nfc_detail", args=[order.pk])},
        dedupe_key=f"admin-nfc:{order.pk}",
    )
    if sent:
        NFCOrder.objects.filter(pk=order.pk).update(admin_notified_at=timezone.now())
    return sent


STATUS_EMAILS = {
    NFCOrder.DESIGN_IN_PROGRESS: "Our designers have started on your cards",
    NFCOrder.ON_HOLD: "Your NFC order is on hold",
    NFCOrder.PRINTING: "Your NFC cards are being printed",
    NFCOrder.ENCODING_QC: "Your NFC cards are being encoded and tested",
    NFCOrder.READY_FOR_DELIVERY: "Your NFC cards are ready for delivery",
    NFCOrder.READY_FOR_PICKUP: "Your NFC cards are ready for pickup",
    NFCOrder.DISPATCHED: "Your NFC cards are on their way",
    NFCOrder.DELIVERED: "Your NFC cards have been delivered",
    NFCOrder.CANCELLED: "Your NFC order has been cancelled",
    NFCOrder.REFUNDED: "Your NFC order has been refunded",
    NFCOrder.DESIGN_PENDING: "Your NFC order is in the design queue",
}


class TransitionError(Exception):
    pass


@transaction.atomic
def change_status(order, new_status, actor=None, note="", notify=True, force=False):
    """Move an order and email the customer about it (AC-13)."""
    order = NFCOrder.objects.select_for_update().get(pk=order.pk)
    if not force and new_status not in NFCOrder.TRANSITIONS.get(order.status, []):
        raise TransitionError(
            f"An order that is {order.get_status_display().lower()} cannot move to "
            f"{dict(NFCOrder.STATUSES)[new_status].lower()}."
        )
    if new_status in (NFCOrder.READY_FOR_DELIVERY, NFCOrder.READY_FOR_PICKUP, NFCOrder.DISPATCHED):
        if not (order.qc_android_ok and order.qc_iphone_ok and order.tags_locked):
            raise TransitionError(
                "Record the Android and iPhone tap tests and confirm the tags are locked before this step (NFC-05, NFC-06)."
            )
    if new_status == NFCOrder.DISPATCHED and not order.courier_name:
        raise TransitionError("Enter the courier name (and tracking number if there is one) first (DLV-05).")
    if new_status == NFCOrder.READY_FOR_PICKUP and not order.is_pickup:
        raise TransitionError("This order is for delivery, not pickup.")
    if new_status == NFCOrder.READY_FOR_DELIVERY and order.is_pickup:
        raise TransitionError("This order is for pickup, not delivery.")

    previous = order.status
    order.status = new_status
    if new_status == NFCOrder.ON_HOLD:
        order.awaiting_since = None
    order.save()
    log(order, note, new_status, actor)
    AuditLog.record(actor, "nfc_status_change", order, reason=note, previous=previous, new=new_status)
    if notify and new_status in STATUS_EMAILS:
        transaction.on_commit(
            lambda: email_customer(
                order, STATUS_EMAILS[new_status], "nfc_status_update",
                {"note": note}, dedupe_key=f"nfc-status:{order.pk}:{new_status}:{order.events.count()}",
            )
        )
    return order


@transaction.atomic
def add_proof(order, file, actor, note=""):
    order = NFCOrder.objects.select_for_update().get(pk=order.pk)
    if order.status in NFCOrder.PRINTING_STARTED | {NFCOrder.CANCELLED, NFCOrder.REFUNDED, NFCOrder.PENDING_PAYMENT}:
        raise TransitionError("A design proof cannot be added at this stage.")
    version = (order.proofs.order_by("-version").values_list("version", flat=True).first() or 0) + 1
    proof = DesignProof.objects.create(order=order, version=version, file=file, staff_note=note, uploaded_by=actor)
    order.status = NFCOrder.AWAITING_APPROVAL
    order.awaiting_since = timezone.now()
    order.save()
    log(order, f"Design proof {version} is ready for your approval.", NFCOrder.AWAITING_APPROVAL, actor)
    AuditLog.record(actor, "nfc_proof_uploaded", order, version=version)
    transaction.on_commit(
        lambda: email_customer(
            order, f"Please review design proof {version} for {order.order_number}",
            "nfc_proof_ready", {"proof": proof}, dedupe_key=f"nfc-proof:{proof.pk}",
        )
    )
    return proof


@transaction.atomic
def approve_proof(order, proof, user):
    order = NFCOrder.objects.select_for_update().get(pk=order.pk)
    if order.status != NFCOrder.AWAITING_APPROVAL or proof.decision != DesignProof.PENDING:
        raise TransitionError("This proof is not waiting for approval.")
    proof.decision = DesignProof.APPROVED
    proof.decided_at = timezone.now()
    proof.save()
    order.status = NFCOrder.APPROVED
    order.awaiting_since = None
    order.save()
    log(order, f"You approved design proof {proof.version}.", NFCOrder.APPROVED, user)
    transaction.on_commit(
        lambda: notify_admins(
            f"Design approved — {order.order_number}", "admin_nfc_decision",
            {"order": order, "proof": proof, "approved": True}, dedupe_key=f"nfc-approved:{proof.pk}",
        )
    )
    return order


@transaction.atomic
def request_changes(order, proof, user, comment):
    order = NFCOrder.objects.select_for_update().get(pk=order.pk)
    if order.status != NFCOrder.AWAITING_APPROVAL or proof.decision != DesignProof.PENDING:
        raise TransitionError("This proof is not waiting for approval.")
    proof.decision = DesignProof.CHANGES
    proof.customer_comment = comment
    proof.decided_at = timezone.now()
    proof.save()
    order.revision_rounds += 1
    order.status = NFCOrder.REVISION_REQUESTED
    order.awaiting_since = None
    order.save()
    free = SiteSettings.load().free_revision_rounds
    extra = order.revision_rounds > free
    log(order, f"You asked for changes to proof {proof.version}: {comment}", NFCOrder.REVISION_REQUESTED, user)
    transaction.on_commit(
        lambda: notify_admins(
            f"Changes requested — {order.order_number}", "admin_nfc_decision",
            {"order": order, "proof": proof, "approved": False, "beyond_free": extra, "free_rounds": free},
            dedupe_key=f"nfc-changes:{proof.pk}",
        )
    )
    return order


def revisions_left(order):
    return max(0, SiteSettings.load().free_revision_rounds - order.revision_rounds)


@transaction.atomic
def cancel_by_customer(order, user, reason):
    """RFD-01..03. The refund itself is processed by Finance."""
    order = NFCOrder.objects.select_for_update().get(pk=order.pk)
    if not order.customer_can_cancel:
        raise TransitionError("Printing has started, so this order can no longer be cancelled (RFD-03).")
    refundable = order.refundable_minor
    order.status = NFCOrder.CANCELLED
    order.cancel_reason = reason
    order.save()
    log(order, f"Cancelled by the customer. Refund due: GHS {refundable / 100:,.2f}. {reason}", NFCOrder.CANCELLED, user)
    AuditLog.record(user, "nfc_cancelled_by_customer", order, reason=reason, refundable_minor=refundable)
    transaction.on_commit(
        lambda: notify_admins(
            f"Order cancelled — {order.order_number}", "admin_nfc_cancelled",
            {"order": order, "refundable_minor": refundable}, dedupe_key=f"nfc-cancel:{order.pk}",
        )
    )
    return order, refundable


def run_proof_reminders():
    """NFC-03: remind after 3 and 7 days; On Hold after 14."""
    site = SiteSettings.load()
    now = timezone.now()
    held = reminded = 0
    for order in NFCOrder.objects.filter(status=NFCOrder.AWAITING_APPROVAL, awaiting_since__isnull=False):
        waited = (now - order.awaiting_since).days
        if waited >= site.on_hold_after_days:
            change_status(order, NFCOrder.ON_HOLD, note="No response to the design proof for 14 days.", force=True)
            held += 1
            continue
        for day in site.proof_reminder_day_list:
            if waited >= day:
                proof = order.latest_proof
                if email_customer(
                    order, f"Reminder: your design proof for {order.order_number} is waiting",
                    "nfc_proof_reminder", {"proof": proof, "days": day},
                    dedupe_key=f"nfc-remind:{order.pk}:{proof.pk if proof else 0}:{day}",
                ):
                    reminded += 1
    return reminded, held


def expire_unpaid_orders(hours=72):
    cutoff = timezone.now() - timedelta(hours=hours)
    return NFCOrder.objects.filter(status=NFCOrder.PENDING_PAYMENT, created_at__lt=cutoff).update(
        status=NFCOrder.CANCELLED, cancel_reason="Payment was not completed."
    )
