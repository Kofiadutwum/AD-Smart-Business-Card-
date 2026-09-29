"""New prices (October 2026): Basic GHS 200 with 5 social links, Professional
GHS 350, Business GHS 700 for up to 5 people, then priced per person in bands.

Only plans that already exist are changed; a fresh database gets the same
prices from ``seed_initial``. People who have already paid keep what they paid
for; the new prices apply to new purchases and renewals.
"""

from django.db import migrations

PRICES = {
    "basic": {"price_minor": 20000, "max_social_links": 5},
    "professional": {"price_minor": 35000},
    "business": {"price_minor": 70000, "max_cards": 5,
                 "blurb": "Your whole team under one brand. The bigger the team, the less each person costs."},
}
# (first person, last person or None, yearly price per person in pesewas)
BUSINESS_BANDS = [(6, 10, 14000), (11, 20, 13000), (21, 50, 12000), (51, 100, 10500), (101, None, 10000)]


def forwards(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    SeatBand = apps.get_model("billing", "SeatBand")
    for code, fields in PRICES.items():
        Plan.objects.filter(code=code).update(**fields)
    business = Plan.objects.filter(code="business").first()
    if business and not SeatBand.objects.filter(plan=business).exists():
        SeatBand.objects.bulk_create(
            SeatBand(plan=business, min_seats=low, max_seats=high, unit_price_minor=price)
            for low, high, price in BUSINESS_BANDS
        )


class Migration(migrations.Migration):
    dependencies = [("billing", "0003_team_pricing")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
