"""Ghana and outside Ghana kept apart in the staff figures, because the prices differ.

The owner's rule (Nov 2026): what was bought decides. A plan paid once is a Ghana plan (the one-time
prices); a yearly plan is from outside Ghana. Pay-when-you-update and pre-made cards are sold in Ghana
shops, and card-update payments only exist for those and for plans paid once. An NFC order follows its
delivery: a zone outside Ghana makes it outside Ghana. Customers with neither a plan nor a pay-when-you-
update card are in neither region (they show under "All" only).

Before the one-time prices began (17 Nov 2026) nothing was marked paid once, and when they began the Ghana
customers' plans were moved to paid once. So a payment made, or a customer who joined, before then counts as
Ghana unless the customer is still on a yearly plan: a yearly plan always means outside Ghana (owner, 27 Nov
2026, after a Netherlands customer from before then was shown as Ghana).
"""

from datetime import datetime
from datetime import timezone as dt_timezone

from django.db.models import Q

from apps.billing.models import Payment
from apps.cards.models import Card

REGIONS = (("ghana", "Ghana"), ("abroad", "Outside Ghana"))
ONE_TIME_PRICES_FROM = datetime(2026, 11, 17, tzinfo=dt_timezone.utc)  # Accra is UTC+0


def update_owners():
    """Customers with a pay-when-you-update card. They are live with no plan, so they are never drafts."""
    return Card.objects.filter(update_plan__isnull=False, deleted_at__isnull=True, owner__isnull=False).values("owner")


def customers_q(region, prefix=""):
    """Customers (or rows whose `prefix` leads to a customer) of one region."""
    ghana = (
        Q(**{f"{prefix}subscription_record__paid_once": True})
        | Q(**{f"{prefix}pk__in": update_owners()})
        | Q(**{f"{prefix}date_joined__lt": ONE_TIME_PRICES_FROM, f"{prefix}subscription_record__isnull": True})
    )
    if region == "ghana":
        return ghana
    return Q(**{f"{prefix}subscription_record__paid_once": False}) & ~Q(**{f"{prefix}pk__in": update_owners()})


def payments_q(region, prefix=""):
    """Payments (or rows whose `prefix` leads to a payment) of one region."""
    ghana = (
        Q(**{f"{prefix}paid_once": True})
        | Q(**{f"{prefix}purpose__in": [Payment.PURPOSE_CARD_UPDATE, Payment.PURPOSE_PREMADE]})
        | (Q(**{f"{prefix}purpose": Payment.PURPOSE_NFC}) & ~Q(**{f"{prefix}nfc_order__delivery_zone__is_international": True}))
        | (Q(**{f"{prefix}created_at__lt": ONE_TIME_PRICES_FROM}) & ~Q(**{f"{prefix}user__subscription_record__paid_once": False}))
    )
    return ghana if region == "ghana" else ~ghana


def cards_q(region, prefix=""):
    """Cards (or rows whose `prefix` leads to a card) of one region, by what their owner bought."""
    ghana = (
        Q(**{f"{prefix}update_plan__isnull": False})
        | Q(**{f"{prefix}owner__subscription_record__paid_once": True})
        | Q(**{f"{prefix}owner__date_joined__lt": ONE_TIME_PRICES_FROM, f"{prefix}owner__subscription_record__isnull": True})
    )
    if region == "ghana":
        return ghana
    return Q(**{f"{prefix}update_plan__isnull": True, f"{prefix}owner__subscription_record__paid_once": False})


def orders_q(region):
    """NFC orders of one region, by where they are delivered."""
    abroad = Q(delivery_zone__is_international=True)
    return ~abroad if region == "ghana" else abroad
