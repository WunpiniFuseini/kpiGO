"""Django settings. Everything an install varies is an environment variable."""

import os
import re
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


def env_secret(name: str) -> str | None:
    """A secret from NAME, or from the file NAME_FILE names (the host's secret mount)."""
    value = env(name)
    path = env(f"{name}_FILE")
    if value is None and path:
        try:
            value = Path(path).read_text(encoding="utf-8").strip() or None
        except OSError as exc:
            raise RuntimeError(f"{name}_FILE is set but {path} cannot be read.") from exc
    return value


DEBUG = env_bool("KPIGO_DEBUG", False)
TESTING = env_bool("KPIGO_TESTING", False)
SECRET_KEY = env("KPIGO_SECRET_KEY") or ("dev-insecure-key" if DEBUG or TESTING else None)
if not SECRET_KEY:
    raise RuntimeError("KPIGO_SECRET_KEY must be set outside development.")

ALLOWED_HOSTS = env_list("KPIGO_ALLOWED_HOSTS", "localhost,127.0.0.1,app,testserver")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "ninja",
    "kpigo.platform",
    "kpigo.periods",
    "kpigo.hierarchy",
    "kpigo.metrics",
    "kpigo.ingestion",
    "kpigo.access",
    "kpigo.licence",
    "kpigo.scorecards",
    "kpigo.agents",
    "kpigo.campaigns",
    "kpigo.executive",
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
# Idle timeout: the cookie lasts this long after the last request. A sign-in also
# ends after KPIGO_SESSION_MAX_HOURS however active it is.
SESSION_COOKIE_AGE = int(env("KPIGO_SESSION_IDLE_MINUTES", "30") or 30) * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
KPIGO_SESSION_MAX_SECONDS = int(env("KPIGO_SESSION_MAX_HOURS", "12") or 12) * 3600

# Local accounts hash with Argon2id (TDD §9).
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

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

# --- Licence (PRD OP-2, TDD §12.1) ------------------------------------------
# licence.activate writes the signed file here; at start, its modules register.
KPIGO_LICENCE_FILE = env("KPIGO_LICENCE_FILE", "/var/lib/kpigo/licence/kpigo.lic")
# Development and test installs run without a licence, with the modules below.
# Everywhere else an install without a licence is limited to setup and the
# licence screen. Deliberately not an environment switch.
KPIGO_LICENCE_ENFORCED = not (DEBUG or TESTING)
KPIGO_ENTITLED_MODULES = env_list(
    "KPIGO_ENTITLED_MODULES", "scorecards,agent_performance,campaign,executive"
)
# The only outbound call kpiGo makes. Unset: no heartbeat (air-gapped installs).
KPIGO_LICENCE_HEARTBEAT_URL = env("KPIGO_LICENCE_HEARTBEAT_URL")

# Maker-checker is per action class and off by default (PRD AD-3). Admins turn
# classes on with approval.policy.set; classes listed here are forced on.
KPIGO_APPROVAL_CLASSES_ENABLED = env_list("KPIGO_APPROVAL_CLASSES_ENABLED", "")

# --- Sign-in (TDD §9, PRD AD-8) ----------------------------------------------
# One-time token for setup.bootstrap, which creates the first Admin.
KPIGO_SETUP_TOKEN = env_secret("KPIGO_SETUP_TOKEN")
# Email + password for local accounts (Argon2id). LDAP binds use the same form.
KPIGO_LOCAL_LOGIN = env_bool("KPIGO_LOCAL_LOGIN", True)
# Sign-in method new users get unless the Admin picks another: local|ldap|oidc|saml.
KPIGO_DEFAULT_AUTH_PROVIDER = env("KPIGO_DEFAULT_AUTH_PROVIDER", "local")
KPIGO_LOGIN_MAX_ATTEMPTS = int(env("KPIGO_LOGIN_MAX_ATTEMPTS", "5") or 5)
KPIGO_LOGIN_LOCKOUT_MINUTES = int(env("KPIGO_LOGIN_LOCKOUT_MINUTES", "15") or 15)
KPIGO_INVITE_DAYS = int(env("KPIGO_INVITE_DAYS", "7") or 7)
# OIDC (Entra ID, Okta, Keycloak...): authorization code + PKCE.
KPIGO_OIDC_ISSUER = env("KPIGO_OIDC_ISSUER")
KPIGO_OIDC_CLIENT_ID = env("KPIGO_OIDC_CLIENT_ID")
KPIGO_OIDC_CLIENT_SECRET = env_secret("KPIGO_OIDC_CLIENT_SECRET")
# The frontend route the identity provider returns to, e.g. https://kpigo.bank/auth/callback
KPIGO_OIDC_REDIRECT_URI = env("KPIGO_OIDC_REDIRECT_URI")
KPIGO_OIDC_SCOPES = env("KPIGO_OIDC_SCOPES", "openid email profile") or "openid email profile"
KPIGO_OIDC_EMAIL_CLAIM = env("KPIGO_OIDC_EMAIL_CLAIM", "email") or "email"
KPIGO_OIDC_REQUIRE_MFA = env_bool("KPIGO_OIDC_REQUIRE_MFA", False)
KPIGO_OIDC_ACR_VALUES = env("KPIGO_OIDC_ACR_VALUES")
KPIGO_OIDC_LABEL = env("KPIGO_OIDC_LABEL", "Single sign-on") or "Single sign-on"
# SAML 2.0: SP-initiated, redirect binding out, POST binding back.
KPIGO_SAML_IDP_ENTITY_ID = env("KPIGO_SAML_IDP_ENTITY_ID")
KPIGO_SAML_IDP_SSO_URL = env("KPIGO_SAML_IDP_SSO_URL")
KPIGO_SAML_IDP_CERT = env("KPIGO_SAML_IDP_CERT")
KPIGO_SAML_IDP_CERT_FILE = env("KPIGO_SAML_IDP_CERT_FILE")
KPIGO_SAML_SP_ENTITY_ID = env("KPIGO_SAML_SP_ENTITY_ID")
# Where the IdP posts: https://<host>/api/v1/auth/saml/acs
KPIGO_SAML_ACS_URL = env("KPIGO_SAML_ACS_URL")
KPIGO_SAML_EMAIL_ATTRIBUTE = env("KPIGO_SAML_EMAIL_ATTRIBUTE")
KPIGO_SAML_REQUIRED_AUTHN_CONTEXT = env("KPIGO_SAML_REQUIRED_AUTHN_CONTEXT")
KPIGO_SAML_LABEL = env("KPIGO_SAML_LABEL", "Single sign-on (SAML)") or "Single sign-on (SAML)"
# On-prem LDAP / Active Directory: password sign-in by bind, and roster import.
KPIGO_LDAP_URL = env("KPIGO_LDAP_URL")
KPIGO_LDAP_BIND_DN = env("KPIGO_LDAP_BIND_DN")
KPIGO_LDAP_BIND_PASSWORD = env_secret("KPIGO_LDAP_BIND_PASSWORD")
KPIGO_LDAP_USER_BASE = env("KPIGO_LDAP_USER_BASE")
KPIGO_LDAP_USER_FILTER = (
    env("KPIGO_LDAP_USER_FILTER", "(&(objectClass=person)(mail={email}))")
    or "(&(objectClass=person)(mail={email}))"
)
KPIGO_LDAP_ATTR_STAFF_NO = env("KPIGO_LDAP_ATTR_STAFF_NO", "employeeID") or "employeeID"
KPIGO_LDAP_ATTR_EMAIL = env("KPIGO_LDAP_ATTR_EMAIL", "mail") or "mail"
KPIGO_LDAP_ATTR_NAME = env("KPIGO_LDAP_ATTR_NAME", "displayName") or "displayName"
KPIGO_LDAP_ATTR_MANAGER = env("KPIGO_LDAP_ATTR_MANAGER", "manager") or "manager"
KPIGO_LDAP_TIMEOUT_SECONDS = int(env("KPIGO_LDAP_TIMEOUT_SECONDS", "10") or 10)
# Entra ID roster import through Microsoft Graph (application permission User.Read.All).
KPIGO_ENTRA_TENANT_ID = env("KPIGO_ENTRA_TENANT_ID")
KPIGO_ENTRA_CLIENT_ID = env("KPIGO_ENTRA_CLIENT_ID")
KPIGO_ENTRA_CLIENT_SECRET = env_secret("KPIGO_ENTRA_CLIENT_SECRET")
KPIGO_ENTRA_AUTHORITY = (
    env("KPIGO_ENTRA_AUTHORITY", "https://login.microsoftonline.com")
    or "https://login.microsoftonline.com"
)
KPIGO_ENTRA_GRAPH_URL = (
    env("KPIGO_ENTRA_GRAPH_URL", "https://graph.microsoft.com") or "https://graph.microsoft.com"
)
KPIGO_ENTRA_STAFF_NO_ATTRIBUTE = env("KPIGO_ENTRA_STAFF_NO_ATTRIBUTE", "employeeId") or "employeeId"

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

# --- Ingestion (TDD §5) ----------------------------------------------------
# Fernet keys for source credentials: comma-separated, first one encrypts. Or a
# file on the host's secret mount, one key per line. Dev and test installs with
# neither derive a key from SECRET_KEY; production must configure one.
KPIGO_CREDENTIAL_KEYS = env_list("KPIGO_CREDENTIAL_KEYS", "")
KPIGO_CREDENTIAL_KEY_FILE = env("KPIGO_CREDENTIAL_KEY_FILE")
KPIGO_CREDENTIAL_DEV_KEY = DEBUG or env_bool("KPIGO_TESTING")
# Drop-folder feeds name a folder under this root; nothing outside it is read.
KPIGO_DROP_ROOT = env("KPIGO_DROP_ROOT", "/var/lib/kpigo/drop")
# A dropped file is picked up once it has not changed for this long.
KPIGO_DROP_SETTLE_SECONDS = int(env("KPIGO_DROP_SETTLE_SECONDS", "30") or 30)
# Every read stops at the row cap (a load above it fails, never truncates) and
# every pull runs under a statement timeout. A connection may set lower ones.
KPIGO_INGEST_ROW_CAP = int(env("KPIGO_INGEST_ROW_CAP", "1000000") or 1_000_000)
KPIGO_PULL_TIMEOUT_SECONDS = int(env("KPIGO_PULL_TIMEOUT_SECONDS", "300") or 300)
KPIGO_UPLOAD_MAX_BYTES = int(env("KPIGO_UPLOAD_MAX_BYTES", str(50 * 1024 * 1024)) or 0)
# Run feed.tick (drop folders, cadences, freshness) as this user every minute.
if env("KPIGO_INGESTION_SCHEDULE_USER"):
    KPIGO_SCHEDULED_ACTIONS.append(
        {
            "action": "feed.tick",
            "run_as": env("KPIGO_INGESTION_SCHEDULE_USER"),
            "every_seconds": int(env("KPIGO_INGESTION_TICK_SECONDS", "60") or 60),
            "params": {},
        }
    )

# Outbound mail goes only to the install's own relay, and only when one is set.
# Unset, kpiGo sends no email: reminders stay in-app and in the audit log.
KPIGO_EMAIL_HOST = env("KPIGO_EMAIL_HOST")
KPIGO_EMAIL_FROM = env("KPIGO_EMAIL_FROM")
if KPIGO_EMAIL_HOST and not KPIGO_EMAIL_FROM:
    raise RuntimeError("KPIGO_EMAIL_HOST is set, so KPIGO_EMAIL_FROM must name the sender.")
KPIGO_EMAIL_SECURITY = (env("KPIGO_EMAIL_SECURITY", "starttls") or "starttls").lower()
if KPIGO_EMAIL_SECURITY not in ("starttls", "tls", "none"):
    raise RuntimeError("KPIGO_EMAIL_SECURITY must be starttls, tls or none.")
EMAIL_HOST = KPIGO_EMAIL_HOST or "localhost"
EMAIL_PORT = int(env("KPIGO_EMAIL_PORT", "465" if KPIGO_EMAIL_SECURITY == "tls" else "587") or 587)
EMAIL_HOST_USER = env("KPIGO_EMAIL_USER", "") or ""
EMAIL_HOST_PASSWORD = env_secret("KPIGO_EMAIL_PASSWORD") or ""
EMAIL_USE_TLS = KPIGO_EMAIL_SECURITY == "starttls"
EMAIL_USE_SSL = KPIGO_EMAIL_SECURITY == "tls"
EMAIL_TIMEOUT = int(env("KPIGO_EMAIL_TIMEOUT_SECONDS", "30") or 30)
DEFAULT_FROM_EMAIL = KPIGO_EMAIL_FROM or "kpigo@localhost"
# The address people open kpiGo at, for links in email; optional.
KPIGO_PUBLIC_URL = (env("KPIGO_PUBLIC_URL", "") or "").rstrip("/")

# Run the manual-input escalation ladder as this user; it checks daily who is due.
if env("KPIGO_INPUT_REMINDER_USER"):
    KPIGO_SCHEDULED_ACTIONS.append(
        {
            "action": "input.remind",
            "run_as": env("KPIGO_INPUT_REMINDER_USER"),
            "every_seconds": 24 * 3600,
            "params": {},
        }
    )

# Take a backup daily as this user, when one is named (PRD OP-5).
KPIGO_BACKUP_DIR = env("KPIGO_BACKUP_DIR", "/var/lib/kpigo/backups")
if env("KPIGO_BACKUP_USER"):
    KPIGO_SCHEDULED_ACTIONS.append(
        {
            "action": "system.backup",
            "run_as": env("KPIGO_BACKUP_USER"),
            "every_seconds": 24 * 3600,
            "params": {"kind": "scheduled"},
        }
    )

# Send the licence heartbeat daily as this user, when a heartbeat URL is set.
if KPIGO_LICENCE_HEARTBEAT_URL and env("KPIGO_LICENCE_HEARTBEAT_USER"):
    KPIGO_SCHEDULED_ACTIONS.append(
        {
            "action": "licence.heartbeat",
            "run_as": env("KPIGO_LICENCE_HEARTBEAT_USER"),
            "every_seconds": 24 * 3600,
            "params": {},
        }
    )

# --- Agent Performance daily retention (PRD AP-12) ---------------------------
# Months of daily detail kept hot; older months roll up to monthly and their
# partition moves to the archive schema (never deleted). Per install, since
# retention obligations differ by regulator.
KPIGO_DAILY_HOT_MONTHS = int(env("KPIGO_DAILY_HOT_MONTHS", "24") or 24)
if KPIGO_DAILY_HOT_MONTHS < 3:
    raise RuntimeError("KPIGO_DAILY_HOT_MONTHS must be at least 3.")
# Optional tablespace for archived partitions (for example on cheaper storage).
KPIGO_ARCHIVE_TABLESPACE = env("KPIGO_ARCHIVE_TABLESPACE") or None
if KPIGO_ARCHIVE_TABLESPACE and not re.fullmatch(r"[a-z_][a-z0-9_]*", KPIGO_ARCHIVE_TABLESPACE):
    raise RuntimeError("KPIGO_ARCHIVE_TABLESPACE must be a plain lower-case identifier.")
# Archive months past the hot window daily, as this user.
if env("KPIGO_RETENTION_USER"):
    KPIGO_SCHEDULED_ACTIONS.append(
        {
            "action": "agent.daily.archive",
            "run_as": env("KPIGO_RETENTION_USER"),
            "every_seconds": 24 * 3600,
            "params": {},
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
