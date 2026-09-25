"""NFC production: design, approvals, printing, encoding, QC, delivery (Figures 6A-6D)."""

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.models import AuditLog, SiteSettings
from apps.nfc import services
from apps.nfc.models import NFCOrder

from ..forms import NoteForm, ProofForm, QualityForm, StatusForm
from ..permissions import can, staff_required

QUEUES = {
    "new": [NFCOrder.PAYMENT_CONFIRMED, NFCOrder.DESIGN_PENDING],
    "design": [NFCOrder.DESIGN_IN_PROGRESS, NFCOrder.REVISION_REQUESTED],
    "waiting": [NFCOrder.AWAITING_APPROVAL, NFCOrder.ON_HOLD],
    "production": [NFCOrder.APPROVED, NFCOrder.PRINTING, NFCOrder.ENCODING_QC],
    "dispatch": [NFCOrder.READY_FOR_DELIVERY, NFCOrder.READY_FOR_PICKUP, NFCOrder.DISPATCHED],
    "done": [NFCOrder.DELIVERED],
    "closed": [NFCOrder.CANCELLED, NFCOrder.REFUNDED],
    "unpaid": [NFCOrder.PENDING_PAYMENT],
}


@staff_required("nfc_view")
def orders(request):
    queue = request.GET.get("queue", "open")
    qs = NFCOrder.objects.select_related("user", "delivery_zone").order_by("-created_at")
    if queue == "open":
        qs = qs.filter(status__in=NFCOrder.OPEN_STATUSES)
    elif queue in QUEUES:
        qs = qs.filter(status__in=QUEUES[queue])
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(order_number__icontains=q) | Q(full_name__icontains=q) | Q(user__email__icontains=q) | Q(phone__icontains=q))
    counts = {key: NFCOrder.objects.filter(status__in=statuses).count() for key, statuses in QUEUES.items()}
    counts["open"] = NFCOrder.objects.filter(status__in=NFCOrder.OPEN_STATUSES).count()
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "staff/nfc_orders.html", {"page": page, "queue": queue, "q": q, "counts": counts})


@staff_required("nfc_view")
def order_detail(request, pk):
    order = get_object_or_404(NFCOrder.objects.select_related("user", "delivery_zone", "bundled_plan"), pk=pk)
    return render(
        request,
        "staff/nfc_order.html",
        {
            "order": order,
            "items": order.items.select_related("card"),
            "proofs": order.proofs.all(),
            "events": order.events.select_related("actor"),
            "payments": order.payments.all() if can(request.user, "view_payments") else None,
            "status_form": StatusForm(initial={"status": ""}),
            "proof_form": ProofForm(),
            "quality_form": QualityForm(instance=order),
            "note_form": NoteForm(),
            "free_rounds": SiteSettings.load().free_revision_rounds,
            "can_design": can(request.user, "nfc_design"),
            "can_produce": can(request.user, "nfc_production"),
        },
    )


@staff_required("nfc_production")
@require_POST
def order_status(request, pk):
    order = get_object_or_404(NFCOrder, pk=pk)
    form = StatusForm(request.POST)
    if form.is_valid():
        new_status = form.cleaned_data["status"]
        design_steps = {NFCOrder.DESIGN_PENDING, NFCOrder.DESIGN_IN_PROGRESS, NFCOrder.AWAITING_APPROVAL}
        if new_status in design_steps and not can(request.user, "nfc_design"):
            messages.error(request, "Only the Design Manager can move an order through design.")
        else:
            try:
                services.change_status(order, new_status, request.user, form.cleaned_data["note"])
                messages.success(request, f"Order moved to {dict(NFCOrder.STATUSES)[new_status].lower()}. The customer was emailed.")
            except services.TransitionError as exc:
                messages.error(request, str(exc))
    return redirect("staff:nfc_detail", pk=pk)


@staff_required("nfc_design")
@require_POST
def order_proof(request, pk):
    order = get_object_or_404(NFCOrder, pk=pk)
    form = ProofForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            proof = services.add_proof(order, form.cleaned_data["file"], request.user, form.cleaned_data["note"])
            messages.success(request, f"Proof {proof.version} sent to the customer for approval.")
        except services.TransitionError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, " ".join(e for errors in form.errors.values() for e in errors))
    return redirect("staff:nfc_detail", pk=pk)


@staff_required("nfc_production")
@require_POST
def order_quality(request, pk):
    order = get_object_or_404(NFCOrder, pk=pk)
    form = QualityForm(request.POST, instance=order)
    if form.is_valid():
        order = form.save(commit=False)
        if order.qc_android_ok and order.qc_iphone_ok and not order.qc_at:
            order.qc_by = request.user
            order.qc_at = timezone.now()
        order.save()
        AuditLog.record(
            request.user, "nfc_quality_check", order,
            android=order.qc_android_ok, iphone=order.qc_iphone_ok, locked=order.tags_locked,
            courier=order.courier_name, tracking=order.tracking_number,
        )
        services.log(order, "Quality check and dispatch details updated.", actor=request.user, visible=False)
        messages.success(request, "Production details saved.")
    return redirect("staff:nfc_detail", pk=pk)


@staff_required("nfc_view")
@require_POST
def order_note(request, pk):
    order = get_object_or_404(NFCOrder, pk=pk)
    form = NoteForm(request.POST)
    if form.is_valid():
        visible = form.cleaned_data["visible_to_customer"]
        services.log(order, form.cleaned_data["message"], actor=request.user, visible=visible)
        if visible:
            services.email_customer(order, f"An update on your order {order.order_number}", "nfc_status_update",
                                    {"note": form.cleaned_data["message"]})
        messages.success(request, "Note added.")
    return redirect("staff:nfc_detail", pk=pk)


@staff_required("nfc_view")
def order_file(request, pk, which):
    order = get_object_or_404(NFCOrder, pk=pk)
    field = {"logo": order.logo, "reference": order.reference_design}.get(which)
    if not field:
        raise Http404
    return FileResponse(field.open("rb"), filename=f"{order.order_number}-{which}-{field.name.rsplit('/', 1)[-1]}")
