"""Business pages: Professional shows up to 10 products, Business up to 30.

The two plans that already exist are updated here; a fresh database gets the same
values from ``seed_initial``. Basic keeps no business page.
"""

from django.db import migrations, models

PRODUCTS = {"professional": 10, "business": 30}


def forwards(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    for code, count in PRODUCTS.items():
        Plan.objects.filter(code=code).update(allow_business_page=True, max_products=count)


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0007_auto_renewal'),
    ]

    operations = [
        migrations.AddField(
            model_name='plan',
            name='allow_business_page',
            field=models.BooleanField(default=False, help_text='A landing page for the business, linked from the card.'),
        ),
        migrations.AddField(
            model_name='plan',
            name='max_products',
            field=models.PositiveSmallIntegerField(default=0, help_text='Pictures of products on the business page.'),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
