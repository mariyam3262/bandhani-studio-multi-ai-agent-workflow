from django.apps import AppConfig


class StudioConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "studio"

    def ready(self):
        from . import generator, qc
        from . import signals
