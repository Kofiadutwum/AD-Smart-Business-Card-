from django.apps import AppConfig


class CardsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.cards'

    def ready(self):
        from . import marketplace  # noqa: F401  (registers the signals that refresh the marketplace)
