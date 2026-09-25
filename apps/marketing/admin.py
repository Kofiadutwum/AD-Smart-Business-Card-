from django.apps import apps
from django.contrib import admin

# Superuser-only raw access (the staff area is the day-to-day tool).
for model in apps.get_app_config(__package__.split(".")[-1]).get_models():
    try:
        admin.site.register(model)
    except admin.sites.AlreadyRegistered:
        pass
