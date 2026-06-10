"""WSGI config for the LingoKatutubo Django project."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lingokatutubo_django.settings")

application = get_wsgi_application()
