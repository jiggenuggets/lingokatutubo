import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DJANGO_DEBUG", "1")
os.environ.setdefault("DJANGO_USE_SQLITE", "1")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lingokatutubo_django.settings")

try:
    import django
    from django.apps import apps

    if not apps.ready:
        django.setup()
except Exception:
    # Keep wrappers importable enough to surface the original exception in
    # callers that are not using Django.
    pass
