"""Basic can show a business logo on its card, like the other plans.

Data only: the Basic plan row gets ``allow_logo``. A fresh database gets the same
value from ``seed_initial``.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    apps.get_model("billing", "Plan").objects.filter(code="basic").update(allow_logo=True)


def backwards(apps, schema_editor):
    apps.get_model("billing", "Plan").objects.filter(code="basic").update(allow_logo=False)


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0011_unlimited_products_and_links"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
