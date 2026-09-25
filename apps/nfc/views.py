"""Customer side of NFC orders (Figures 6A-6D)."""

import io

from django.contrib import messages
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.decorators import customer_required
from apps.billing import fx
from apps.billing import services as billing
from apps.billing.models import Payment, PromoCode
from apps.billing.paystack import PaymentError
from apps.core.models import SiteSettings

from . import services
from .forms import CancelForm, ChangesForm, NFCOrderForm
from .models import DeliveryZone, DesignProof, NFCOrder, NFCOrderItem


def _order_quote(quantity, zone, plan, promo):
    cards_minor = services.cards_price(quantity)
    lines = [(f"{quantity} customised NFC card{'s' if quantity != 1 else ''}", cards_minor)]
    if zone and zone.fee_minor:
        kind = "Shipping and delivery" if zone.is_international else "Delivery"
        lines.append((f"{kind}: {zone.name}", zone.fee_minor))
    nfc_subtotal = sum(amount for _, amount in lines)
    plan_minor = plan.price_minor if plan else 0
    if plan:
        lines.append((f"{plan.name} plan, 12 months", plan_minor))
    # A promo for NFC orders discounts the cards and delivery; one for both
    # also discounts a plan bought in the same checkout.
    discount = 0
    if promo:
        base = nfc_subtotal + (plan_minor if promo.applies_to == PromoCode.SCOPE_BOTH else 0)
        discount = promo.discount_on(base)
    quote = billing.build_quote(lines, None)
    taxable = quote["subtotal_minor"] - discount
    tax = billing.tax_on(taxable)
    quote.update(
        {"discount_minor": discount, "tax_minor": tax, "total_minor": taxable + tax, "promo": promo,
         "cards_minor": cards_minor, "plan_minor": plan_minor}
    )
    return quote


@customer_required
def order(request):
    user = request.user
    cards = list(user.cards.filter(deleted_at__isnull=True).order_by("created_at"))
    if not cards:
        messages.info(request, "Create your digital card first. Your NFC cards will open it.")
        return redirect("dashboard:editor")
    subscription = user.subscription
    needs_plan = not (subscription and subscription.is_live)
    plan_limit = subscription.plan.max_cards if subscription else 1
    order_cards = cards if (subscription and subscription.plan.max_cards > 1) else cards[:1]
    plans = billing.active_plans()
    first = cards[0]
    initial = {
        "full_name": first.full_name,
        "business_name": first.business_name,
        "position": first.job_title,
        "email": first.email or user.email,
        "phone": (first.phones.first().number if first.phones.exists() else user.phone) or "",
        "website": first.website,
        "recipient_name": user.full_name or first.full_name,
        "recipient_phone": user.phone or "",
        "plan": plans.filter(is_featured=True).first() if needs_plan else None,
    }
    form = NFCOrderForm(
        request.POST or None, request.FILES or None, cards=order_cards, needs_plan=needs_plan, plans=plans,
        initial=initial,
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        promo, promo_error = billing.resolve_promo(data.get("promo"), "nfc")
        if promo_error:
            form.add_error("promo", promo_error)
        else:
            plan = data.get("plan") if needs_plan else None
            try:
                quote = _order_quote(data["quantity"], data["delivery_zone"], plan, promo)
            except ValueError as exc:
                form.add_error(None, str(exc))
                quote = None
            if quote:
                with transaction.atomic():
                    nfc_order = NFCOrder.objects.create(
                        order_number=services.new_order_number(),
                        user=user,
                        quantity=data["quantity"],
                        cards_minor=quote["cards_minor"],
                        delivery_fee_minor=data["delivery_zone"].fee_minor,
                        plan_minor=quote["plan_minor"],
                        discount_minor=quote["discount_minor"],
                        tax_minor=quote["tax_minor"],
                        total_minor=quote["total_minor"],
                        bundled_plan=plan,
                        full_name=data["full_name"],
                        business_name=data["business_name"],
                        position=data["position"],
                        email=data["email"],
                        phone=data["phone"],
                        website=data["website"],
                        logo=data.get("logo") or "",
                        reference_design=data.get("reference_design") or "",
                        design_description=data["design_description"],
                        notes=data["notes"],
                        delivery_zone=data["delivery_zone"],
                        recipient_name=data["recipient_name"],
                        recipient_phone=data["recipient_phone"],
                        delivery_address=data["delivery_address"],
                        delivery_city=data["delivery_city"],
                        delivery_postcode=data["delivery_postcode"],
                        delivery_country=data["delivery_country"] or "GH",
                        delivery_gps=data["delivery_gps"],
                        landmark=data["landmark"],
                        accepted_refund_policy_at=timezone.now(),
                    )
                    for card, qty in data["items"]:
                        NFCOrderItem.objects.create(order=nfc_order, card=card, quantity=qty)
                    services.log(nfc_order, "Order placed. Waiting for payment.", NFCOrder.PENDING_PAYMENT, user)
                    payment = billing.create_payment(
                        user=user, purpose=Payment.PURPOSE_NFC, quote=quote, plan=plan,
                        months=plan.duration_months if plan else 0, nfc_order=nfc_order,
                    )
                try:
                    return redirect(billing.checkout_url(payment, request))
                except PaymentError as exc:
                    Payment.objects.filter(pk=payment.pk).update(status=Payment.FAILED)
                    messages.error(request, f"{exc} Your order is saved; you can pay from the order page.")
                    return redirect("nfc:detail", number=nfc_order.order_number)

    return render(
        request,
        "nfc/order.html",
        {
            "active": "nfc",
            "form": form,
            "needs_plan": needs_plan,
            "plan_limit": plan_limit,
            "tiers_json": services.tiers_json(),
            "zones": DeliveryZone.objects.filter(is_active=True),
            "zones_json": {
                z.pk: {"fee": z.fee_minor, "pickup": z.is_pickup, "intl": z.is_international, "eta": z.eta}
                for z in DeliveryZone.objects.filter(is_active=True)
            },
            "plans_json": {p.pk: p.price_minor for p in plans},
            "tax_rate": str(SiteSettings.load().tax_rate_percent),
            "rate": fx.latest_rate(),
        },
    )


def callback(request):
    reference = request.GET.get("reference") or request.GET.get("trxref")
    payment = Payment.objects.select_related("nfc_order").filter(reference=reference).first() if reference else None
    if payment is None or payment.nfc_order is None:
        messages.error(request, "We could not find that payment. If money left your account, contact support.")
        return redirect("dashboard:home" if request.user.is_authenticated else "marketing:home")
    status = billing.verify_and_fulfil(payment, "callback")
    if status == Payment.SUCCESS:
        messages.success(request, "Payment received. Your NFC order is confirmed.")
    elif status == Payment.PENDING:
        messages.info(request, "Your payment is still being confirmed. This page updates when it is.")
    else:
        messages.error(request, "That payment did not go through. You have not been charged.")
    if not request.user.is_authenticated:
        return redirect("accounts:login")
    return redirect("nfc:detail", number=payment.nfc_order.order_number)


def _own_order(request, number):
    nfc_order = get_object_or_404(NFCOrder.objects.select_related("delivery_zone", "user"), order_number=number)
    if nfc_order.user_id != request.user.pk:
        raise Http404
    return nfc_order


PROGRESS = [
    ("Paid", {NFCOrder.PAYMENT_CONFIRMED, NFCOrder.DESIGN_PENDING}),
    ("Design", {NFCOrder.DESIGN_IN_PROGRESS, NFCOrder.AWAITING_APPROVAL, NFCOrder.REVISION_REQUESTED, NFCOrder.ON_HOLD}),
    ("Approved", {NFCOrder.APPROVED}),
    ("Printing", {NFCOrder.PRINTING, NFCOrder.ENCODING_QC}),
    ("On its way", {NFCOrder.READY_FOR_DELIVERY, NFCOrder.READY_FOR_PICKUP, NFCOrder.DISPATCHED}),
    ("Delivered", {NFCOrder.DELIVERED}),
]


@customer_required
def detail(request, number):
    nfc_order = _own_order(request, number)
    if nfc_order.status == NFCOrder.PENDING_PAYMENT:
        pending = nfc_order.payments.filter(status=Payment.PENDING).first()
        if pending and request.GET.get("check"):
            billing.verify_and_fulfil(pending, "order-page")
            nfc_order.refresh_from_db()
    reached = -1
    for index, (_, statuses) in enumerate(PROGRESS):
        if nfc_order.status in statuses:
            reached = index
    return render(
        request,
        "nfc/detail.html",
        {
            "active": "nfc",
            "order": nfc_order,
            "items": nfc_order.items.select_related("card"),
            "proofs": nfc_order.proofs.all(),
            "events": nfc_order.events.filter(visible_to_customer=True),
            "progress": [(label, i <= reached) for i, (label, _) in enumerate(PROGRESS)],
            "changes_form": ChangesForm(),
            "cancel_form": CancelForm(),
            "revisions_left": services.revisions_left(nfc_order),
            "payment": nfc_order.payments.filter(status=Payment.SUCCESS).first(),
        },
    )


@customer_required
@require_POST
def pay(request, number):
    """Retry payment for an order still waiting for it."""
    nfc_order = _own_order(request, number)
    if nfc_order.status != NFCOrder.PENDING_PAYMENT:
        return redirect("nfc:detail", number=number)
    previous = nfc_order.payments.order_by("-created_at").first()
    quote = {
        "lines": previous.line_items if previous else [],
        "subtotal_minor": previous.subtotal_minor if previous else nfc_order.total_minor,
        "discount_minor": nfc_order.discount_minor,
        "tax_minor": nfc_order.tax_minor,
        "total_minor": nfc_order.total_minor,
        "promo": previous.promo_code if previous else None,
    }
    payment = billing.create_payment(
        user=request.user, purpose=Payment.PURPOSE_NFC, quote=quote, plan=nfc_order.bundled_plan,
        months=nfc_order.bundled_plan.duration_months if nfc_order.bundled_plan else 0, nfc_order=nfc_order,
    )
    try:
        return redirect(billing.checkout_url(payment, request))
    except PaymentError as exc:
        Payment.objects.filter(pk=payment.pk).update(status=Payment.FAILED)
        messages.error(request, f"{exc} You have not been charged.")
        return redirect("nfc:detail", number=number)


@customer_required
@require_POST
def approve(request, number, version):
    nfc_order = _own_order(request, number)
    proof = get_object_or_404(DesignProof, order=nfc_order, version=version)
    try:
        services.approve_proof(nfc_order, proof, request.user)
        messages.success(request, "Design approved. We will start printing and encoding your cards.")
    except services.TransitionError as exc:
        messages.error(request, str(exc))
    return redirect("nfc:detail", number=number)


@customer_required
@require_POST
def request_changes(request, number, version):
    nfc_order = _own_order(request, number)
    proof = get_object_or_404(DesignProof, order=nfc_order, version=version)
    form = ChangesForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Tell us what to change.")
        return redirect("nfc:detail", number=number)
    try:
        services.request_changes(nfc_order, proof, request.user, form.cleaned_data["comment"])
        left = services.revisions_left(nfc_order)
        if left:
            messages.success(request, f"Thanks. Our designers will send a new proof. You have {left} free round{'s' if left != 1 else ''} of changes left.")
        else:
            messages.warning(request, "Thanks. This is beyond the free revision rounds, so our team may contact you about a small fee.")
    except services.TransitionError as exc:
        messages.error(request, str(exc))
    return redirect("nfc:detail", number=number)


@customer_required
@require_POST
def cancel(request, number):
    nfc_order = _own_order(request, number)
    form = CancelForm(request.POST)
    form.is_valid()
    try:
        _, refundable = services.cancel_by_customer(nfc_order, request.user, form.cleaned_data.get("reason", ""))
        messages.success(
            request,
            f"Order cancelled. A refund of GHS {refundable / 100:,.2f} will be processed to your original payment method."
            if refundable else "Order cancelled.",
        )
    except services.TransitionError as exc:
        messages.error(request, str(exc))
    return redirect("nfc:detail", number=number)


def proof_file(request, number, version):
    """Owner or staff only; proofs are not public."""
    if not request.user.is_authenticated:
        raise Http404
    nfc_order = get_object_or_404(NFCOrder, order_number=number)
    if nfc_order.user_id != request.user.pk and not request.user.is_staff:
        raise Http404
    proof = get_object_or_404(DesignProof, order=nfc_order, version=version)
    handle = proof.file.open("rb")
    name = proof.file.name.rsplit("/", 1)[-1]
    return FileResponse(handle, filename=f"{nfc_order.order_number}-proof-{version}-{name}")


def approved_pdf(request, number):
    """NFC-07: the approved design as a PDF."""
    if not request.user.is_authenticated:
        raise Http404
    nfc_order = get_object_or_404(NFCOrder, order_number=number)
    if nfc_order.user_id != request.user.pk and not request.user.is_staff:
        raise Http404
    proof = nfc_order.approved_proof
    if proof is None:
        raise Http404
    filename = f"{nfc_order.order_number}-approved-design.pdf"
    if proof.is_pdf:
        return FileResponse(proof.file.open("rb"), filename=filename, content_type="application/pdf")
    from PIL import Image
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    with proof.file.open("rb") as handle:
        image = Image.open(handle)
        image.load()
    buffer = io.BytesIO()
    page = landscape(A4) if image.width >= image.height else A4
    pdf = canvas.Canvas(buffer, pagesize=page)
    margin = 36
    box_w, box_h = page[0] - 2 * margin, page[1] - 2 * margin - 24
    scale = min(box_w / image.width, box_h / image.height)
    w, h = image.width * scale, image.height * scale
    pdf.setFont("Helvetica", 10)
    pdf.drawString(margin, page[1] - margin, f"{nfc_order.order_number} · approved design (proof {proof.version}) · {proof.decided_at:%d %b %Y}")
    pdf.drawImage(ImageReader(image.convert("RGB")), (page[0] - w) / 2, margin, width=w, height=h)
    pdf.showPage()
    pdf.save()
    return HttpResponse(buffer.getvalue(), content_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})
