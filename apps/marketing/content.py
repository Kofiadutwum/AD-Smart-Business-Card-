"""Marketing copy that is data rather than layout: FAQ and the steps.

Figures in answers (grace days, revision rounds, prices) are filled in from
the live settings at render time, so they never drift from what the system
actually does.
"""

HOW_IT_WORKS = [
    {
        "icon": "user-plus",
        "title": "Create your account",
        "text": "Sign up with your email and confirm it. It takes a minute.",
    },
    {
        "icon": "square-pen",
        "title": "Build and preview your card",
        "text": "Add your photo, numbers, WhatsApp, socials and location. Watch it update as you type.",
    },
    {
        "icon": "credit-card",
        "title": "Choose a yearly plan",
        "text": "Pay with Mobile Money or a Visa or Mastercard card through Paystack's secure checkout.",
    },
    {
        "icon": "share-2",
        "title": "Share it everywhere",
        "text": "Your permanent link, QR code and NFC card all open the same card, which you can update any time.",
    },
]

SHARING = [
    {"icon": "link", "title": "A link that never changes", "text": "Paste it into WhatsApp, email signatures and bios. Edit your details and the link still works."},
    {"icon": "qr-code", "title": "A QR code for print", "text": "Download it as PNG or SVG for flyers, banners, shopfronts and name tags."},
    {"icon": "nfc", "title": "An NFC card that taps", "text": "A printed card with a chip. Tap it on a phone and your card opens — no app needed."},
    {"icon": "contact", "title": "Save contact in one tap", "text": "Recipients save your photo, numbers and email straight into their phone's contacts."},
]


def faq(site, plans=None, tiers=None):
    grace = site.grace_period_days
    rounds = site.free_revision_rounds
    return [
        {
            "q": "How does an NFC card work?",
            "a": "Each card has a small chip programmed with the permanent link to your digital card. "
                 "When someone holds their phone near it, the phone opens your card in its browser. "
                 "Nothing is installed, and the chip needs no battery.",
        },
        {
            "q": "Does it work on both iPhone and Android?",
            "a": "Yes. iPhone XS and newer read NFC tags automatically with the screen on. Most Android phones "
                 "read them when NFC is switched on in settings. If a phone cannot tap, the QR code on the card "
                 "opens the same page.",
        },
        {
            "q": "What happens when I change my details?",
            "a": "Your link, QR code and NFC card never change. They always open your latest details, so there "
                 "is nothing to reprint when you change job, number or photo.",
        },
        {
            "q": "How does renewal work?",
            "a": "Plans are yearly. We email you 30 days, 7 days and 1 day before your plan ends. Renew early and "
                 "the new year is added to the end of your current one, so you lose nothing. "
                 f"If you miss the date your card keeps working for a {grace}-day grace period.",
        },
        {
            "q": "What if my plan runs out?",
            "a": "After the grace period your card shows a short 'card inactive' page instead of your details. "
                 "Renew at any time and the same link, QR code and NFC cards work again straight away.",
        },
        {
            "q": "How long does an NFC order take?",
            "a": "You receive a first design proof within 3 working days of payment, and "
                 f"{rounds} rounds of changes are free. Printing and encoding take up to 5 working "
                 "days after you approve, then delivery or pickup.",
        },
        {
            "q": "Do you deliver outside Ghana?",
            "a": "Yes. We deliver anywhere in Ghana by courier, you can pick up from our office for free, and we "
                 "ship abroad. For orders outside Ghana you pay the international shipping and delivery fee for "
                 "your region at checkout; any import duties or taxes in your country are paid by the recipient. "
                 "You see the fee and the estimated time before you pay.",
        },
        {
            "q": "Can I cancel an NFC order?",
            "a": "Before design work starts you get a full refund. After design starts and before printing, 50% of "
                 "the card price is refundable. Once printing starts an order cannot be cancelled. A card with a "
                 "faulty chip reported within 30 days is replaced free.",
        },
        {
            "q": "Why is the price shown in dollars?",
            "a": "The US dollar figure is a guide for international customers, worked out from today's exchange "
                 "rate. You are always charged in Ghana cedis, and your bank may use its own rate.",
        },
        {
            "q": "Which payment methods can I use?",
            "a": "MTN MoMo, Telecel Cash and AirtelTigo Money, plus Visa and Mastercard cards. Payments go through "
                 "Paystack's secure checkout; we never see your card or wallet details.",
        },
    ]
