"""Django settings. Everything an install varies is an environment variable."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def env_bool(name: str, default: bool = False) -> bool:
    value = env(name)
    return default if value is None else value.lower() in ("1", "true", "yes", "on")


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in (env(name, default) or "").split(",") if item.strip()]


DEBUG = env_bool("KPIGO_DEBUG", False)
SECRET_KEY = env("KPIGO_SECRET_KEY") or (
    "dev-insecure-key" if DEBUG or env_bool("KPIGO_TESTING") else None
)
if not SECRET_KEY:
    raise RuntimeError("KPIGO_SECRET_KEY must be set outside development.")

ALLOWED_HOSTS = env_list("KPIGO_ALLOWED_HOSTS", "localhost,127.0.0.1,app,testserver")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "ninja",
    "kpigo.platform",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "kpigo.urls"
WSGI_APPLICATION = "kpigo.wsgi.application"
ASGI_APPLICATION = "kpigo.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "kpigo"),
        "USER": env("POSTGRES_USER", "kpigo"),
        "PASSWORD": env("POSTGRES_PASSWORD", "kpigo"),
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = env_bool("KPIGO_SECURE_COOKIES", not DEBUG)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE

# --- Celery (broker, result backend) -------------------------------------
REDIS_URL = env("REDIS_URL", "redis://localhost:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_ALWAYS_EAGER = env_bool("KPIGO_CELERY_EAGER", False)
CELERY_TASK_EAGER_PROPAGATES = True

# --- kpiGo ---------------------------------------------------------------
# Single tenant per install today; the column costs nothing (Schema conventions).
KPIGO_ORG_ID = env("KPIGO_ORG_ID", "00000000-0000-0000-0000-000000000001")

# Modules the licence entitles. Until the licence service exists (R0 Workstream D)
# this is configuration; unlisted modules' actions do not register.
KPIGO_ENTITLED_MODULES = env_list(
    "KPIGO_ENTITLED_MODULES", "scorecards,agent_performance,campaign,executive"
)

# Maker-checker is per action class and off by default (PRD AD-3).
KPIGO_APPROVAL_CLASSES_ENABLED = env_list("KPIGO_APPROVAL_CLASSES_ENABLED", "")

# Global maintenance flag the action wrapper honours (TDD §12.1).
KPIGO_MAINTENANCE_MODE = env_bool("KPIGO_MAINTENANCE_MODE", False)

# Actions run on a schedule by the job adapter. Each entry:
# {"action": "platform.hello", "run_as": "<username>", "every_seconds": 3600, "params": {}}
KPIGO_SCHEDULED_ACTIONS: list[dict[str, object]] = []
if env("KPIGO_HELLO_SCHEDULE_USER"):
    KPIGO_SCHEDULED_ACTIONS.append(
        {
            "action": "platform.hello",
            "run_as": env("KPIGO_HELLO_SCHEDULE_USER"),
            "every_seconds": int(env("KPIGO_HELLO_SCHEDULE_SECONDS", "3600") or 3600),
            "params": {"name": "scheduler"},
        }
    )

# Inference is optional and absent by default (TDD §13). The app must work
# with this unset; CI boots the stack without it and runs the full suite.
KPIGO_INFERENCE_PROVIDER = env("KPIGO_INFERENCE_PROVIDER")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "kpigo.logs.JsonFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": env("KPIGO_LOG_LEVEL", "INFO")},
}
