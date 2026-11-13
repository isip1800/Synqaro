from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        from django.core.signals import request_started

        from .models import Company

        request_started.connect(lambda **kwargs: Company.clear_cache(), weak=False,
                                dispatch_uid="core.company_cache")

        from . import audit

        audit.connect()
