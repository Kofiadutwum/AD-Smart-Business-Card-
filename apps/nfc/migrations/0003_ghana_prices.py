from django.db import migrations, models

# Visitors in Ghana, each band priced on its own: the first 10 cards GHS 150 each, cards 11-20 GHS 130 each,
# every card above 20 GHS 115. A bigger order therefore never costs less than a smaller one.
GHANA_TIERS = [
    dict(min_quantity=1, max_quantity=10, unit_price_minor=15000, mode="flat"),
    dict(min_quantity=11, max_quantity=20, unit_price_minor=13000, mode="incremental"),
    dict(min_quantity=21, max_quantity=None, unit_price_minor=11500, mode="incremental"),
]


def add_ghana_tiers(apps, schema_editor):
    PriceTier = apps.get_model("nfc", "PriceTier")
    if not PriceTier.objects.filter(ghana=True).exists():
        for data in GHANA_TIERS:
            PriceTier.objects.create(ghana=True, **data)


def remove_ghana_tiers(apps, schema_editor):
    apps.get_model("nfc", "PriceTier").objects.filter(ghana=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("nfc", "0002_international_delivery"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="pricetier",
            options={"ordering": ["ghana", "min_quantity"]},
        ),
        migrations.AddField(
            model_name="pricetier",
            name="ghana",
            field=models.BooleanField(
                default=False,
                help_text="Tick for the price table shown to visitors in Ghana. Without any ticked row they pay the common table.",
            ),
        ),
        migrations.RunPython(add_ghana_tiers, remove_ghana_tiers),
    ]
