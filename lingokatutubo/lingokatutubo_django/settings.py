"""Django settings for the unified LingoKatutubo application."""

from pathlib import Path
from urllib.parse import urlparse
import os


BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("DJANGO_DEBUG", "0").lower() in {"1", "true", "yes", "on"}
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-lingokatutubo-secret-key-change-me"
    else:
        raise RuntimeError("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is disabled.")

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]
ALLOW_DEBUG_WILDCARD_HOST = (
    os.environ.get("DJANGO_ALLOW_DEBUG_WILDCARD_HOST", "0").lower()
    in {"1", "true", "yes", "on"}
)
if DEBUG and ALLOW_DEBUG_WILDCARD_HOST and "*" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append("*")


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "translator",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "lingokatutubo_django.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "translator.context_processors.active_translation_jobs",
            ],
        },
    },
]

WSGI_APPLICATION = "lingokatutubo_django.wsgi.application"


def _database_from_url(url: str) -> dict:
    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("DATABASE_URL must use postgres:// or postgresql://")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/"),
        "USER": parsed.username or "",
        "PASSWORD": parsed.password or "",
        "HOST": parsed.hostname or "localhost",
        "PORT": str(parsed.port or 5432),
    }


def _database_config() -> dict:
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return _database_from_url(database_url)

    if os.environ.get("DJANGO_USE_SQLITE", "").lower() in {"1", "true", "yes", "on"}:
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ.get("SQLITE_DATABASE_PATH", BASE_DIR / "db.sqlite3"),
            # Background translation jobs run on a worker thread (see
            # LINGOKATUTUBO_TASK_MODE=thread) and write to the DB concurrently
            # with the request thread. SQLite's default DEFERRED transactions
            # acquire the write lock lazily on the first write, so two threads
            # that both start with a read (e.g. update_or_create) can race for
            # the upgrade and get "database is locked" immediately, without
            # waiting out the busy timeout. transaction_mode="IMMEDIATE" makes
            # every transaction grab the write lock upfront so the second
            # writer queues (up to `timeout` seconds) instead of erroring.
            "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"},
        }

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "lingokatutubo"),
        "USER": os.environ.get("POSTGRES_USER", "postgres"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "postgres"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }


DATABASES = {"default": _database_config()}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "Asia/Singapore")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = Path(
    os.environ.get("LINGOKATUTUBO_MEDIA_ROOT", BASE_DIR / "media")
).resolve()

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "translator:login"
LOGIN_REDIRECT_URL = "translator:translate"
LOGOUT_REDIRECT_URL = "translator:home"

# JSON API routes (see translator.views.JSON_API_PATH_PREFIXES) need a JSON
# CSRF failure body so fetch() callers don't choke parsing Django's HTML CSRF
# error page as JSON. Normal HTML form posts keep the default page.
CSRF_FAILURE_VIEW = "translator.views.csrf_failure"

# Email — used for the password-reset workflow. Defaults to the console
# backend so reset emails are visible in the dev server log when no SMTP
# settings are configured. For production, set DJANGO_EMAIL_BACKEND to
# "django.core.mail.backends.smtp.EmailBackend" and provide EMAIL_HOST,
# EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, EMAIL_USE_TLS, and
# DEFAULT_FROM_EMAIL.
EMAIL_BACKEND = os.environ.get(
    "DJANGO_EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend"
    if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1").lower() in {"1", "true", "yes", "on"}
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "no-reply@lingokatutubo.local")

DATA_UPLOAD_MAX_MEMORY_SIZE = 55 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
LINGOKATUTUBO_MAX_UPLOAD_BYTES = int(
    os.environ.get("LINGOKATUTUBO_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))
)
LINGOKATUTUBO_TASK_MODE = os.environ.get("LINGOKATUTUBO_TASK_MODE", "thread")
LINGOKATUTUBO_TASK_TIMEOUT_SECONDS = int(
    os.environ.get("LINGOKATUTUBO_TASK_TIMEOUT_SECONDS", "900")
)
LINGOKATUTUBO_OCR_TIMEOUT_SECONDS = int(
    os.environ.get("LINGOKATUTUBO_OCR_TIMEOUT_SECONDS", "120")
)

# Experimental ByT5 Bagobo-Tagabawa -> English neural fallback.
# DISABLED by default. When enabled, it is consulted only for segments the
# phrasebook/dataset cascade could not translate, and only for the
# configured source -> target direction. Every neural output is marked
# needs_review. Requires the optional torch/transformers stack and the model
# files to be present; if either is missing the pipeline falls back to the
# phrasebook.
LINGOKATUTUBO_NEURAL_TRANSLATION_ENABLED = os.environ.get(
    "LINGOKATUTUBO_NEURAL_TRANSLATION_ENABLED", "0"
).lower() in {"1", "true", "yes", "on"}
LINGOKATUTUBO_BYT5_MODEL_DIR = os.environ.get(
    "LINGOKATUTUBO_BYT5_MODEL_DIR",
    str(BASE_DIR / "model_artifacts" / "byt5_tagabawa_english_full_v1"),
)
LINGOKATUTUBO_BYT5_SOURCE_LANGUAGE = os.environ.get(
    "LINGOKATUTUBO_BYT5_SOURCE_LANGUAGE", "tagabawa"
)
LINGOKATUTUBO_BYT5_TARGET_LANGUAGE = os.environ.get(
    "LINGOKATUTUBO_BYT5_TARGET_LANGUAGE", "english"
)

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = LINGOKATUTUBO_TASK_TIMEOUT_SECONDS
CELERY_TASK_SOFT_TIME_LIMIT = max(1, LINGOKATUTUBO_TASK_TIMEOUT_SECONDS - 30)
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"


def _enable_sqlite_wal(sender, connection, **kwargs):
    """Let translation-job writes from the worker thread queue instead of
    raising 'database is locked' against the request thread (see OPTIONS
    timeout above for why this matters under LINGOKATUTUBO_TASK_MODE=thread).
    """
    if connection.vendor != "sqlite":
        return
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")


from django.db.backends.signals import connection_created  # noqa: E402

connection_created.connect(_enable_sqlite_wal)
