from django.apps import apps
from django.contrib import admin

from .models import Card


@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    # The token Paystack gave us for charging an owner's card for changes is not shown to anyone.
    exclude = ("update_authorization_code",)


# Superuser-only raw access (the staff area is the day-to-day tool).
for model in apps.get_app_config(__package__.split(".")[-1]).get_models():
    try:
        admin.site.register(model)
    except admin.sites.AlreadyRegistered:
        pass
