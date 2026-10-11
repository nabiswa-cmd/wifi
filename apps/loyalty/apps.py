from django.apps import AppConfig


class LoyaltyConfigApp(AppConfig):
    name = 'apps.loyalty'
    label = 'loyalty'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        from . import signals  # noqa: F401
