"""Public card pages (Figures 5A and 5B)."""

import json

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.core.emails import notify_admins, send_email
from apps.core.models import SiteSettings
from apps.core.utils import rate_limited, visitor_hash

from . import services
from .colors import palette
from .forms import LeadForm, ReportForm
from .models import CardEvent


def card_context(card, request=None, lead_form=None, **extra):
    eff = card.effective()
    context = {
        "card": card,
        "eff": eff,
        "pal": palette(eff["accent"], eff["card_colour"]),
        "card_url": card.public_url(),
        "vcf_url": card.get_absolute_url() + "/card.vcf" + _src_suffix(request),
        "qr_svg": services.qr_svg(card.public_url("qr"), scale=5),
        "lead_form": lead_form,
    }
    context.update(extra)
    return context


def _src_suffix(request):
    if request is None:
        return ""
    source = services.source_from(request)
    return f"?src={source}" if source != "link" else ""


def _lookup(request, slug, suffix=""):
    card, current = services.resolve_slug(slug)
    if card is None:
        return None, render(request, "cards/state.html", {"state": "missing"}, status=404)
    if current:
        # URL-05: an old slug permanently redirects, keeping ?src= for analytics.
        query = request.META.get("QUERY_STRING")
        target = f"/c/{current}{suffix}" + (f"?{query}" if query else "")
        return None, redirect(target, permanent=True)
    return card, None


def _state_response(request, card):
    state = card.public_state()
    if state == card.STATE_LIVE:
        return None
    status = 410 if state == card.STATE_DELETED else 200
    return render(request, "cards/state.html", {"state": state, "card": card}, status=status)


def public_card(request, slug):
    card, response = _lookup(request, slug)
    if response:
        return response
    blocked = _state_response(request, card)
    if blocked:
        return blocked
    services.record(request, card, CardEvent.VIEW)
    lead_form = LeadForm(auto_id="lead_%s") if card.collect_leads and SiteSettings.load().leads_enabled else None
    return render(request, "cards/public.html", card_context(card, request, lead_form))


def vcf(request, slug):
    card, response = _lookup(request, slug, "/card.vcf")
    if response:
        return response
    if card.public_state() != card.STATE_LIVE:
        return _state_response(request, card)
    services.record(request, card, CardEvent.SAVE_CONTACT)
    payload = services.build_vcard(card, card.effective())
    return HttpResponse(
        payload,
        content_type="text/vcard; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{services.vcard_filename(card)}"',
            "Cache-Control": "no-store",
        },
    )


def qr(request, slug, fmt):
    """QR images for the public (and for the owner's downloads, SHR-03)."""
    if fmt not in ("png", "svg"):
        raise Http404
    card, response = _lookup(request, slug, f"/qr.{fmt}")
    if response:
        return response
    if card.public_state() == card.STATE_DELETED:
        return _state_response(request, card)
    url = card.public_url("qr")
    filename = f"{card.slug}-qr.{fmt}"
    disposition = "attachment" if request.GET.get("download") else "inline"
    if fmt == "svg":
        body = services.qr_svg(url, scale=10, light="#ffffff")
        content_type = "image/svg+xml"
    else:
        scale = request.GET.get("scale", "")
        body = services.qr_png(url, scale=min(int(scale), 40) if scale.isdigit() and int(scale) > 0 else 12)
        content_type = "image/png"
    return HttpResponse(
        body, content_type=content_type, headers={"Content-Disposition": f'{disposition}; filename="{filename}"'}
    )


@require_POST
def connect(request, slug):
    card, response = _lookup(request, slug)
    if response:
        return response
    if card.public_state() != card.STATE_LIVE or not (card.collect_leads and SiteSettings.load().leads_enabled):
        return redirect(card.get_absolute_url())
    first_name = card.full_name.split()[0]
    if request.POST.get("website_url"):  # honeypot
        messages.success(request, f"Thank you. {first_name} has your details.")
        return redirect(card.get_absolute_url())
    form = LeadForm(request.POST, auto_id="lead_%s")
    if not form.is_valid():
        return render(
            request, "cards/public.html", card_context(card, request, form, open_lead_form=True), status=400
        )
    fingerprint = visitor_hash(request)
    if rate_limited(f"lead:{card.pk}:{fingerprint}", 3, 3600):
        messages.warning(request, "You have already sent your details. Try again in an hour.")
        return redirect(card.get_absolute_url())
    lead = form.save(commit=False)
    lead.card = card
    lead.source = services.source_from(request)
    lead.visitor_hash = fingerprint
    lead.save()
    send_email(
        to=card.owner.email,
        subject=f"{lead.name} shared their details with you",
        template="new_lead",
        context={"lead": lead, "card": card, "user": card.owner},
        user=card.owner,
        kind="new_lead",
    )
    messages.success(request, f"Thank you. {first_name} has your details.")
    return redirect(card.get_absolute_url())


def report(request, slug):
    card, response = _lookup(request, slug, "/report")
    if response:
        return response
    form = ReportForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if rate_limited(f"report:{visitor_hash(request)}", 5, 3600):
            messages.error(request, "You have sent several reports already. Try again later.")
            return redirect(card.get_absolute_url())
        report_obj = form.save(commit=False)
        report_obj.card = card
        report_obj.visitor_hash = visitor_hash(request)
        report_obj.save()
        notify_admins(
            f"Card reported: {card.slug}", "admin_card_report",
            {"report": report_obj, "card": card, "staff_url": settings.SITE_URL + "/staff/reports/"},
        )
        messages.success(request, "Thank you. Our support team will review this card.")
        return redirect(card.get_absolute_url())
    return render(request, "cards/report.html", {"form": form, "card": card})


@csrf_exempt
@require_POST
def event(request, slug):
    """Click beacons from the public card (Section 11). No personal data."""
    card, response = _lookup(request, slug)
    if card is None or card.public_state() != card.STATE_LIVE:
        return HttpResponse(status=204)
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return HttpResponseBadRequest()
    kind = data.get("kind")
    allowed = {k for k, _ in CardEvent.KINDS} - {CardEvent.VIEW, CardEvent.SAVE_CONTACT}
    if kind not in allowed:
        return HttpResponse(status=204)
    source = data.get("src") if data.get("src") in {"link", "qr", "nfc"} else "link"
    if not rate_limited(f"evt:{card.pk}:{visitor_hash(request)}", 60, 600):
        services.record(request, card, kind, detail=str(data.get("detail", ""))[:32], source=source)
    return HttpResponse(status=204)
