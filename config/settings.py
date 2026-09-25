"""Settings for AD Smart Business Cards.

Every secret and every environment-specific value is read from the
environment (see .env.example). Nothing sensitive lives in this file, so the
same code runs in development, staging and production (SRS PRJ-01, SEC-11).
"""

import os
from pathlib import Path
from urllib.parse import urlparse

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


# Hosting dashboards sometimes insist on a value; these all mean "not set".
UNSET = {"", "none", "null", "-", "n/a", "skip"}


def env(name, default=""):
    value = os.environ.get(name)
    if value is None or value.strip().lower() in UNSET:
        return default
    return value.strip()


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------

DEBUG = env_bool("DEBUG", False)

SECRET_KEY = env("SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError("SECRET_KEY must be set when DEBUG is off.")
    SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"

# The public address of the site. Card URLs, QR codes, NFC tags and emails
# are all built from this, so it must be the production domain in production.
SITE_URL = env("SITE_URL", "http://127.0.0.1:8000").rstrip("/")
SITE_NAME = env("SITE_NAME", "AD Smart Business Cards")
SITE_SHORT_NAME = env("SITE_SHORT_NAME", "AD Smart")

_site_host = urlparse(SITE_URL).hostname or "localhost"
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS") or [_site_host, "localhost", "127.0.0.1"]
if env("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(env("RENDER_EXTERNAL_HOSTNAME"))

CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS") or [
    f"{urlparse(SITE_URL).scheme}://{_site_host}"
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "anymail",
    "django_countries",
    "apps.core",
    "apps.accounts",
    "apps.billing",
    "apps.cards",
    "apps.nfc",
    "apps.marketing",
    "apps.support",
    "apps.dashboard",
    "apps.staff",
]

MIDDLEWARE = [
    "apps.core.middleware.HealthCheckMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
    "apps.staff.middleware.StaffAccessMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site",
            ],
            "builtins": ["apps.core.templatetags.ui"],
        },
    },
]

# --------------------------------------------------------------------------
# Database and cache
# --------------------------------------------------------------------------

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
        conn_health_checks=True,
    )
}

# A database-backed cache so rate limits are shared by every worker process.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Accounts and passwords
# --------------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "marketing:home"

# Argon2 for new passwords (SEC-02). The Werkzeug hasher only verifies hashes
# imported from the old Flask site and upgrades them on the next login.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "apps.accounts.hashers.WerkzeugPasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
STAFF_IDLE_TIMEOUT_SECONDS = 30 * 60  # SEC-05

GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = env("GOOGLE_CLIENT_SECRET")

# --------------------------------------------------------------------------
# Localisation (NFR-09: Ghana time, "GHS 1,500.00")
# --------------------------------------------------------------------------

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Africa/Accra"
USE_I18N = False
USE_TZ = True
CURRENCY = "GHS"

# --------------------------------------------------------------------------
# Static files and uploads
# --------------------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

CLOUDINARY_URL = env("CLOUDINARY_URL")

STORAGES = {
    "default": {
        "BACKEND": (
            "apps.core.storage.CloudinaryMediaStorage"
            if CLOUDINARY_URL
            else "django.core.files.storage.FileSystemStorage"
        ),
    },
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

# SEC-07: images up to 5 MB; logos and reference designs up to 10 MB.
MAX_IMAGE_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_DESIGN_UPLOAD_BYTES = 10 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# --------------------------------------------------------------------------
# Email (NOT-01)
# --------------------------------------------------------------------------

RESEND_API_KEY = env("RESEND_API_KEY")
if RESEND_API_KEY:
    EMAIL_BACKEND = "anymail.backends.resend.EmailBackend"
    ANYMAIL = {"RESEND_API_KEY": RESEND_API_KEY}
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", f"{SITE_NAME} <no-reply@localhost>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
SUPPORT_EMAIL = env("SUPPORT_EMAIL", "adgraphics881@gmail.com")

# --------------------------------------------------------------------------
# Payments (PAY-01..11)
# --------------------------------------------------------------------------

PAYSTACK_SECRET_KEY = env("PAYSTACK_SECRET_KEY")
PAYSTACK_PUBLIC_KEY = env("PAYSTACK_PUBLIC_KEY")
PAYSTACK_BASE_URL = "https://api.paystack.co"
# Sandbox simulates the gateway end to end. It is forced on whenever no
# secret key is configured, so a fresh clone can never reach a live gateway.
PAYMENT_SANDBOX = env_bool("PAYMENT_SANDBOX", True) or not PAYSTACK_SECRET_KEY
PAYMENT_SANDBOX_OUTCOME = env("PAYMENT_SANDBOX_OUTCOME", "success")

# Exchange-rate provider for the USD display (CUR-02).
FX_PROVIDER_URL = env("FX_PROVIDER_URL", "https://open.er-api.com/v6/latest/USD")

# The Flask site signed its visitor hashes with its own secret; new hashes use
# this salt so repeat-visitor counting survives a SECRET_KEY rotation.
ANALYTICS_SALT = env("ANALYTICS_SALT", SECRET_KEY)

# --------------------------------------------------------------------------
# Security (SEC-01, SEC-05)
# --------------------------------------------------------------------------

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", "2592000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False

# --------------------------------------------------------------------------
# Logging (NFR-04)
# --------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
