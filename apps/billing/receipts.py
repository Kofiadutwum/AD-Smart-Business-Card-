"""Receipt PDFs (PAY-08, PAY-09)."""

import io

from django.conf import settings
from django.contrib.staticfiles import finders
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from apps.core.models import SiteSettings
from apps.core.utils import format_ghs

NAVY = colors.HexColor("#0A0E9C")
BLUE = colors.HexColor("#0048A9")
INK = colors.HexColor("#1B1D24")
MUTED = colors.HexColor("#5B6070")
LINE = colors.HexColor("#DADDE5")


def render_receipt_pdf(payment):
    site = SiteSettings.load()
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    left, right = 20 * mm, width - 20 * mm
    y = height - 22 * mm

    logo = finders.find("brand/logo-192.png")
    if logo:
        pdf.drawImage(logo, left, y - 14 * mm, width=18 * mm, height=14 * mm, mask="auto", preserveAspectRatio=True)
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(left + 22 * mm, y - 6 * mm, settings.SITE_NAME)
    pdf.setFont("Helvetica", 9)
    pdf.setFillColor(MUTED)
    pdf.drawString(left + 22 * mm, y - 11 * mm, f"{site.support_email} · {site.support_phone}")

    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawRightString(right, y - 6 * mm, "RECEIPT")
    pdf.setFont("Helvetica", 9)
    pdf.setFillColor(MUTED)
    pdf.drawRightString(right, y - 11 * mm, payment.receipt_number or "")

    y -= 30 * mm
    user = payment.user
    paid = timezone.localtime(payment.paid_at) if payment.paid_at else None
    rows_left = [
        ("Billed to", user.company_name or user.display_name),
        ("", user.company_address),
        ("Email", user.email),
    ]
    rows_right = [
        ("Date", paid.strftime("%d %B %Y, %H:%M") if paid else ""),
        ("Reference", payment.reference),
        ("Method", _method(payment)),
    ]
    for (l_label, l_value), (r_label, r_value) in zip(rows_left, rows_right):
        pdf.setFont("Helvetica", 8.5)
        pdf.setFillColor(MUTED)
        pdf.drawString(left, y, l_label)
        pdf.drawString(width / 2 + 5 * mm, y, r_label)
        pdf.setFont("Helvetica", 10)
        pdf.setFillColor(INK)
        pdf.drawString(left + 22 * mm, y, (l_value or "")[:48])
        pdf.drawString(width / 2 + 27 * mm, y, (r_value or "")[:40])
        y -= 6 * mm

    y -= 6 * mm
    pdf.setFillColor(BLUE)
    pdf.rect(left, y - 2 * mm, right - left, 8 * mm, stroke=0, fill=1)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 9.5)
    pdf.drawString(left + 3 * mm, y + 0.5 * mm, "Item")
    pdf.drawRightString(right - 3 * mm, y + 0.5 * mm, "Amount")
    y -= 9 * mm

    pdf.setFont("Helvetica", 10)
    for item in payment.line_items or [{"label": payment.description, "amount_minor": payment.subtotal_minor}]:
        pdf.setFillColor(INK)
        pdf.drawString(left + 3 * mm, y, str(item.get("label", ""))[:80])
        pdf.drawRightString(right - 3 * mm, y, format_ghs(item.get("amount_minor", 0)))
        y -= 5 * mm
        pdf.setStrokeColor(LINE)
        pdf.line(left, y + 1.5 * mm, right, y + 1.5 * mm)
        y -= 2 * mm

    def total_row(label, minor, bold=False):
        nonlocal y
        pdf.setFont("Helvetica-Bold" if bold else "Helvetica", 11 if bold else 10)
        pdf.setFillColor(INK if bold else MUTED)
        pdf.drawRightString(right - 40 * mm, y, label)
        pdf.setFillColor(INK)
        pdf.drawRightString(right - 3 * mm, y, minor)
        y -= 6 * mm

    y -= 2 * mm
    if payment.discount_minor:
        promo = f" ({payment.promo_code.code})" if payment.promo_code else ""
        total_row(f"Discount{promo}", "−" + format_ghs(payment.discount_minor))
    if payment.tax_minor:
        total_row(site.tax_label or "Tax", format_ghs(payment.tax_minor))
    total_row("Total paid", format_ghs(payment.amount_minor), bold=True)

    y -= 8 * mm
    pdf.setFont("Helvetica", 8.5)
    pdf.setFillColor(MUTED)
    pdf.drawString(left, y, "All amounts are in Ghana cedis (GHS). Thank you for choosing AD Smart Business Cards.")
    pdf.drawString(left, y - 4.5 * mm, settings.SITE_URL)

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _method(payment):
    channel = (payment.channel or "").replace("_", " ")
    if payment.gateway == "manual":
        return f"Manual ({channel})"
    return f"Paystack · {channel.title()}" if channel else "Paystack"
