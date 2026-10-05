from django.apps import apps
from django.contrib import admin

from .models import Subscription


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    # The token Paystack gave us for renewing a customer's card is not shown to anyone.
    exclude = ("authorization_code",)


# Superuser-only raw access (the staff area is the day-to-day tool).
for model in apps.get_app_config(__package__.split(".")[-1]).get_models():
    try:
        admin.site.register(model)
    except admin.sites.AlreadyRegistered:
        pass
