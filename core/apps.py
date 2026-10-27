"""App configuration for ``core``, which holds StravaXEng's models, views and commands."""
from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Registers the ``core`` app with Django."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
