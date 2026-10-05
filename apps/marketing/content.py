"""Marketing copy that is data rather than layout: FAQ and the steps.

Figures in answers (grace days, revision rounds, prices) are filled in from
the live settings at render time, so they never drift from what the system
actually does. Every sentence is translated into the visitor's language.
"""

from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

HOW_IT_WORKS = [
    {
        "icon": "user-plus",
        "title": gettext_lazy("Create your account"),
        "text": gettext_lazy("Sign up with your email and confirm it. It takes a minute."),
    },
    {
        "icon": "square-pen",
        "title": gettext_lazy("Build and preview your card"),
        "text": gettext_lazy("Add your photo, numbers, WhatsApp, socials and location. Watch it update as you type."),
    },
    {
        "icon": "credit-card",
        "title": gettext_lazy("Choose a yearly plan"),
        "text": gettext_lazy("Pay with Mobile Money or a Visa or Mastercard card through Paystack's secure checkout."),
    },
    {
        "icon": "share-2",
        "title": gettext_lazy("Share it everywhere"),
        "text": gettext_lazy("Your permanent link, QR code and NFC card all open the same card, which you can update any time."),
    },
]

SHARING = [
    {"icon": "link", "title": gettext_lazy("A link that never changes"), "text": gettext_lazy("Paste it into WhatsApp, email signatures and bios. Edit your details and the link still works.")},
    {"icon": "qr-code", "title": gettext_lazy("A QR code for print"), "text": gettext_lazy("Download it as PNG or SVG for flyers, banners, shopfronts and name tags.")},
    {"icon": "nfc", "title": gettext_lazy("An NFC card that taps"), "text": gettext_lazy("A printed card with a chip. Tap it on a phone and your card opens \u2014 no app needed.")},
    {"icon": "contact", "title": gettext_lazy("Save contact in one tap"), "text": gettext_lazy("Recipients save your photo, numbers and email straight into their phone's contacts.")},
]


def faq(site, plans=None, tiers=None):
    grace = site.grace_period_days
    rounds = site.free_revision_rounds
    return [
        {
            "q": _("How does an NFC card work?"),
            "a": _("Each card has a small chip programmed with the permanent link to your digital card. "
                   "When someone holds their phone near it, the phone opens your card in its browser. "
                   "Nothing is installed, and the chip needs no battery."),
        },
        {
            "q": _("Does it work on both iPhone and Android?"),
            "a": _("Yes. iPhone XS and newer read NFC tags automatically with the screen on. Most Android phones "
                   "read them when NFC is switched on in settings. If a phone cannot tap, the QR code on the card "
                   "opens the same page."),
        },
        {
            "q": _("What happens when I change my details?"),
            "a": _("Your link, QR code and NFC card never change. They always open your latest details, so there "
                   "is nothing to reprint when you change job, number or photo."),
        },
        {
            "q": _("How does renewal work?"),
            "a": _("Plans are yearly. We email you 30 days, 7 days and 1 day before your plan ends. Renew early and "
                   "the new year is added to the end of your current one, so you lose nothing. "
                   "If you miss the date your card keeps working for a %(grace)d-day grace period.") % {"grace": grace},
        },
        {
            "q": _("What if my plan runs out?"),
            "a": _("After the grace period your card shows a short 'card inactive' page instead of your details. "
                   "Renew at any time and the same link, QR code and NFC cards work again straight away."),
        },
        {
            "q": _("I bought a ready-made card in a shop. How do I start?"),
            "a": _("Tap the card on your phone, or scan its QR code. Enter the PIN printed on the back, add your "
                   "details and create your account. Your first year is already paid for, and the link on the card "
                   "stays the same for good."),
        },
        {
            "q": _("How long does an NFC order take?"),
            "a": _("You receive a first design proof within 3 working days of payment, and "
                   "%(rounds)d rounds of changes are free. Printing and encoding take up to 5 working "
                   "days after you approve, then delivery or pickup.") % {"rounds": rounds},
        },
        {
            "q": _("Do you deliver outside Ghana?"),
            "a": _("Yes. We deliver anywhere in Ghana by courier, you can pick up from our office for free, and we "
                   "ship abroad. For orders outside Ghana you pay the international shipping and delivery fee for "
                   "your region at checkout; any import duties or taxes in your country are paid by the recipient. "
                   "You see the fee and the estimated time before you pay."),
        },
        {
            "q": _("Can I cancel an NFC order?"),
            "a": _("Before design work starts you get a full refund. After design starts and before printing, 50% of "
                   "the card price is refundable. Once printing starts an order cannot be cancelled. A card with a "
                   "faulty chip reported within 30 days is replaced free."),
        },
        {
            "q": _("Why are prices shown in other currencies?"),
            "a": _("Prices are shown in US dollars unless you choose another currency at the top of the page. The "
                   "figure is a guide for international customers, worked out from today's exchange rate. You are "
                   "always charged in Ghana cedis, and your bank may use its own rate."),
        },
        {
            "q": _("Which payment methods can I use?"),
            "a": _("MTN MoMo, Telecel Cash and AirtelTigo Money, plus Visa and Mastercard cards. Payments go through "
                   "Paystack's secure checkout; we never see your card or wallet details."),
        },
    ]
