"""Pre-made cards: make a batch, download the print pack, follow what has been set up."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.cards import premade
from apps.cards.models import PremadeBatch, PremadeCard
from apps.core.models import AuditLog
from apps.core.utils import ghs_to_minor

from ..forms import PremadeBatchForm
from ..permissions import staff_required


def _counts(batch):
    cards = batch.cards.all()
    return {
        "claimed": cards.filter(claimed_at__isnull=False).count(),
        "void": cards.filter(claimed_at__isnull=True, voided_at__isnull=False).count(),
        "waiting": cards.filter(claimed_at__isnull=True, voided_at__isnull=True).count(),
    }


@staff_required("premade_cards")
def batches(request):
    form = PremadeBatchForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            batch = premade.create_batch(
                request.user, name=data["name"], quantity=data["quantity"], plan=data["plan"],
                price_minor=ghs_to_minor(data["price"]), months=data["plan"].duration_months, note=data["note"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, f"{batch.quantity} cards made. Download the print pack now: the PINs are shown only for 24 hours.")
            return redirect("staff:premade_batch", pk=batch.pk)
    rows = []
    for batch in PremadeBatch.objects.select_related("plan", "created_by")[:60]:
        batch.counts = _counts(batch)
        rows.append(batch)
    return render(request, "staff/premade.html", {"form": form, "batches": rows})


@staff_required("premade_cards")
def batch_detail(request, pk):
    batch = get_object_or_404(PremadeBatch.objects.select_related("plan"), pk=pk)
    status = request.GET.get("status", "")
    cards = batch.cards.select_related("card", "claimed_by")
    if status == "claimed":
        cards = cards.filter(claimed_at__isnull=False)
    elif status == "waiting":
        cards = cards.filter(claimed_at__isnull=True, voided_at__isnull=True)
    elif status == "void":
        cards = cards.filter(claimed_at__isnull=True, voided_at__isnull=False)
    page = Paginator(cards, 100).get_page(request.GET.get("page"))
    return render(
        request, "staff/premade_batch.html",
        {"batch": batch, "page": page, "counts": _counts(batch), "pack_ready": premade.get_pack(batch) is not None,
         "status": status, "params": request.GET},
    )


def _pack_or_message(request, batch):
    rows = premade.get_pack(batch)
    if rows is None:
        messages.error(request, "The print pack has expired. Choose “New PINs” to make a fresh one for the cards still waiting.")
    return rows


@staff_required("premade_cards")
def pack_csv(request, pk):
    batch = get_object_or_404(PremadeBatch, pk=pk)
    rows = _pack_or_message(request, batch)
    if rows is None:
        return redirect("staff:premade_batch", pk=batch.pk)
    AuditLog.record(request.user, "premade_pack_downloaded", batch, kind="csv", cards=len(rows))
    return HttpResponse(
        premade.pack_csv(rows), content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{batch.code}-cards.csv"', "Cache-Control": "no-store"},
    )


@staff_required("premade_cards")
def pack_zip(request, pk):
    batch = get_object_or_404(PremadeBatch, pk=pk)
    rows = _pack_or_message(request, batch)
    if rows is None:
        return redirect("staff:premade_batch", pk=batch.pk)
    AuditLog.record(request.user, "premade_pack_downloaded", batch, kind="zip", cards=len(rows))
    return HttpResponse(
        premade.pack_zip(rows), content_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{batch.code}-qr-codes.zip"', "Cache-Control": "no-store"},
    )


@staff_required("premade_cards")
@require_POST
def reissue(request, pk):
    batch = get_object_or_404(PremadeBatch, pk=pk)
    rows = premade.reissue_pins(batch, request.user)
    messages.success(request, f"New PINs made for {len(rows)} waiting cards. The old PINs no longer work. Download the new pack now.")
    return redirect("staff:premade_batch", pk=batch.pk)


@staff_required("premade_cards")
@require_POST
def void(request, pk):
    card = get_object_or_404(PremadeCard.objects.select_related("batch"), pk=pk)
    try:
        premade.void_card(card, request.user, reason=request.POST.get("reason", "").strip())
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    else:
        messages.success(request, f"{card.label} is out of use. Its link now shows “this card no longer exists”.")
    return redirect("staff:premade_batch", pk=card.batch_id)
