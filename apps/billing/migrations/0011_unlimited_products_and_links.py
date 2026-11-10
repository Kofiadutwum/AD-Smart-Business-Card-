"""Bigger allowances: Professional shows up to 50 products, Business has no limit on
products, and Basic has no limit on social media links.

``max_products`` becomes optional (empty means unlimited), the same way
``max_social_links`` already works. The three plans that exist are updated here; a
fresh database gets the same values from ``seed_initial``.
"""

from django.db import migrations, models


def forwards(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.filter(code="professional").update(max_products=50)
    Plan.objects.filter(code="business").update(max_products=None)
    Plan.objects.filter(code="basic").update(max_social_links=None)


def backwards(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.filter(code="professional").update(max_products=10)
    Plan.objects.filter(code="business").update(max_products=30)
    Plan.objects.filter(code="basic").update(max_social_links=5)


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0010_pay_when_you_update"),
    ]

    operations = [
        migrations.AlterField(
            model_name="plan",
            name="max_products",
            field=models.PositiveSmallIntegerField(
                blank=True, default=0, help_text="Products on the business page. Leave empty for unlimited.", null=True
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
