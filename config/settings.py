"""
Django settings for the link resolver project.

Every environment-specific value is read from an environment variable so the
same image runs locally and in Docker without code changes (see .env.example).
"""

import os
import warnings
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# The fallback key is only for local development; Docker passes a real one via .env.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-secret-key-change-me-in-env")
DEBUG = env_bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", default="localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "links",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serves the admin's CSS/JS from STATIC_ROOT, so no nginx container is needed.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.gzip.GZipMiddleware",  # a 1000-row page is ~2 MB of JSON; URLs compress well
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# SQLite is allowed by the task. The path is configurable so Docker can keep
# the file on a volume and survive container restarts.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("SQLITE_PATH", BASE_DIR / "db.sqlite3"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# The image runs collectstatic; a local checkout usually has not, and WhiteNoise
# would warn about the missing folder on every command and test run.
warnings.filterwarnings("ignore", message="No directory at:")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- API ---------------------------------------------------------------------
# Every endpoint is admin-only unless a view says otherwise.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAdminUser"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}

# The task asks for a very long-lived token (3-4 years). That is a trade-off:
# a leaked token stays valid until it expires or SECRET_KEY is rotated (which
# invalidates every token). Production would use a short access token plus the
# refresh endpoint: set JWT_ACCESS_TOKEN_DAYS=0.01 (about 15 minutes).
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(days=float(os.environ.get("JWT_ACCESS_TOKEN_DAYS", "1460"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=float(os.environ.get("JWT_REFRESH_TOKEN_DAYS", "1460"))),
}
