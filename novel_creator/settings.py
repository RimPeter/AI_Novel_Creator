

from pathlib import Path
from email.utils import parseaddr
import importlib.util
import os
import sys

import dj_database_url
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=None):
    value = os.environ.get(name)
    if value is None:
        return list(default or [])
    return [item.strip() for item in value.split(",") if item.strip()]


def env_str(name, default=""):
    value = os.environ.get(name)
    if value is None:
        return default
    cleaned = value.strip()
    if cleaned.lower() in {"", "none", "null", "undefined"}:
        return default
    return cleaned


OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
RUNNING_TESTS = "test" in sys.argv
STRIPE_PUBLISHABLE_KEY = env_str("STRIPE_PUBLISHABLE_KEY")
STRIPE_SECRET_KEY = env_str("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = env_str("STRIPE_WEBHOOK_SECRET")
STRIPE_PRICE_MONTHLY = env_str("STRIPE_PRICE_MONTHLY")
STRIPE_PRICE_YEARLY = env_str("STRIPE_PRICE_YEARLY")
STRIPE_PRICE_SINGLE_MONTH = env_str("STRIPE_PRICE_SINGLE_MONTH")
STRIPE_PRICE_TRIAL_WEEK = env_str("STRIPE_PRICE_TRIAL_WEEK")
STRIPE_BILLING_ENABLED = all(
    [
        STRIPE_PUBLISHABLE_KEY,
        STRIPE_SECRET_KEY,
        STRIPE_WEBHOOK_SECRET,
        STRIPE_PRICE_MONTHLY,
        STRIPE_PRICE_YEARLY,
        STRIPE_PRICE_SINGLE_MONTH,
        STRIPE_PRICE_TRIAL_WEEK,
    ]
)
if RUNNING_TESTS:
    STRIPE_BILLING_ENABLED = False

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-a@59m%nsxzedimgx*61t!@#%pdnv=+4u3!fv@%r1a!p*tm0ipe")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env_bool("DEBUG", True)
if not DEBUG and not RUNNING_TESTS and SECRET_KEY.startswith("django-insecure-"):
    raise RuntimeError("Refusing to start with an insecure default SECRET_KEY while DEBUG is False.")

default_allowed_hosts = [
    "127.0.0.1",
    "localhost",
    "local",
    "ai-novel-manager-80ede991eb1d.herokuapp.com",
    "novel-manager.com",
    "www.novel-manager.com",
]
configured_allowed_hosts = env_list("ALLOWED_HOSTS", [])
ALLOWED_HOSTS = list(dict.fromkeys(configured_allowed_hosts + default_allowed_hosts))

default_csrf_trusted_origins = [
    "https://novel-manager.com",
    "https://www.novel-manager.com",
]
configured_csrf_trusted_origins = env_list("CSRF_TRUSTED_ORIGINS", [])
CSRF_TRUSTED_ORIGINS = list(dict.fromkeys(configured_csrf_trusted_origins + default_csrf_trusted_origins))
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", not DEBUG and not RUNNING_TESTS)
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", not DEBUG and not RUNNING_TESTS)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", not DEBUG and not RUNNING_TESTS)
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "0" if DEBUG or RUNNING_TESTS else "3600"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", not DEBUG and not RUNNING_TESTS)
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", not DEBUG and not RUNNING_TESTS)
SECURE_CONTENT_TYPE_NOSNIFF = env_bool("SECURE_CONTENT_TYPE_NOSNIFF", True)
X_FRAME_OPTIONS = os.environ.get("X_FRAME_OPTIONS", "DENY")
SECURE_REFERRER_POLICY = os.environ.get("SECURE_REFERRER_POLICY", "strict-origin-when-cross-origin")
SESSION_COOKIE_HTTPONLY = env_bool("SESSION_COOKIE_HTTPONLY", True)
SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
CSRF_COOKIE_SAMESITE = os.environ.get("CSRF_COOKIE_SAMESITE", "Lax")
CONTACT_RATE_LIMIT = int(os.environ.get("CONTACT_RATE_LIMIT", "20"))
CONTACT_RATE_WINDOW_SECONDS = int(os.environ.get("CONTACT_RATE_WINDOW_SECONDS", "3600"))
CONTACT_SPIKE_ALERT_THRESHOLD = int(os.environ.get("CONTACT_SPIKE_ALERT_THRESHOLD", "100"))
PROJECT_404_ALERT_THRESHOLD = int(os.environ.get("PROJECT_404_ALERT_THRESHOLD", "25"))
WEBHOOK_SIGNATURE_ALERT_THRESHOLD = int(os.environ.get("WEBHOOK_SIGNATURE_ALERT_THRESHOLD", "20"))
SECURITY_RATE_LIMIT_RULES = {
    "account_login": (10, 900),
    "account_request_login_code": (8, 900),
    "account_reset_password": (8, 900),
    "contact": (CONTACT_RATE_LIMIT, CONTACT_RATE_WINDOW_SECONDS),
    "billing-checkout": (20, 3600),
    "billing-portal": (30, 3600),
    "billing-cancel-recurring": (12, 3600),
    "billing-clear-status": (12, 3600),
    "billing-webhook": (120, 60),
}
USE_S3_MEDIA = env_bool("USE_S3_MEDIA", False)
YOUTUBE_APP_ENABLED = importlib.util.find_spec("youtube") is not None



# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.sites',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'comic_book',
    'security',
    'main',
]
if YOUTUBE_APP_ENABLED:
    INSTALLED_APPS.append("youtube")
if USE_S3_MEDIA:
    INSTALLED_APPS.append("storages")

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'security.middleware.SecurityRateLimitMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'novel_creator.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / "templates"],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'main.context_processors.navbar_text_model',
                'main.context_processors.optional_apps',
            ],
        },
    },
]

WSGI_APPLICATION = 'novel_creator.wsgi.application'

SITE_ID = 1
SITE_DOMAIN = os.environ.get("SITE_DOMAIN", "127.0.0.1:8010" if DEBUG else "localhost")
SITE_NAME = os.environ.get("SITE_NAME", "AI Novel Creator")

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

ACCOUNT_ADAPTER = "main.account_adapter.AccountAdapter"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

ACCOUNT_LOGIN_METHODS = ["username", "email"]
ACCOUNT_SIGNUP_FIELDS = ["username*", "email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_CONFIRM_EMAIL_ON_GET = True
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_LOGIN_BY_CODE_ENABLED = True
ACCOUNT_LOGIN_BY_CODE_SUPPORTS_RESEND = True
ACCOUNT_EMAIL_NOTIFICATIONS = True
ACCOUNT_EMAIL_SUBJECT_PREFIX = "[AI Novel Creator] "
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_DEFAULT_HTTP_PROTOCOL = os.environ.get("ACCOUNT_DEFAULT_HTTP_PROTOCOL", "http" if DEBUG else "https")
ACCOUNT_FORMS = {
    "signup": "main.forms.TestingSignupForm",
    "request_login_code": "main.forms.LegacyVerifiedRequestLoginCodeForm",
    "reset_password": "main.forms.LegacyVerifiedResetPasswordForm",
}

EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND",
    "django.core.mail.backends.filebased.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "").strip()
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "").strip()
EMAIL_TIMEOUT = int(os.environ.get("EMAIL_TIMEOUT", "10"))
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "AI Novel Creator <noreply@localhost>")
SERVER_EMAIL = os.environ.get("SERVER_EMAIL", DEFAULT_FROM_EMAIL)
CONTACT_EMAIL = env_str("CONTACT_EMAIL", parseaddr(SERVER_EMAIL)[1] or parseaddr(DEFAULT_FROM_EMAIL)[1])
EMAIL_FILE_PATH = BASE_DIR / os.environ.get("EMAIL_FILE_PATH", ".local_mail")
if EMAIL_BACKEND == "django.core.mail.backends.filebased.EmailBackend":
    EMAIL_FILE_PATH.mkdir(parents=True, exist_ok=True)


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

default_sqlite_url = f"sqlite:///{(BASE_DIR / 'db.sqlite3').as_posix()}"
database_url = os.environ.get("DATABASE_URL", default_sqlite_url)
database_ssl_required = database_url.startswith(("postgres://", "postgresql://")) and not DEBUG and not RUNNING_TESTS
DATABASES = {
    "default": dj_database_url.config(
        default=database_url,
        conn_max_age=600,
        conn_health_checks=True,
        ssl_require=database_ssl_required,
    )
}


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# CELERY (Redis)
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/1")

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

CELERY_TASK_TIME_LIMIT = 60 * 30
CELERY_TASK_SOFT_TIME_LIMIT = 60 * 25

CELERY_REDIS_SOCKET_CONNECT_TIMEOUT = float(os.environ.get("CELERY_REDIS_SOCKET_CONNECT_TIMEOUT", "1"))
CELERY_REDIS_SOCKET_TIMEOUT = float(os.environ.get("CELERY_REDIS_SOCKET_TIMEOUT", "5"))
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "socket_connect_timeout": CELERY_REDIS_SOCKET_CONNECT_TIMEOUT,
    "socket_timeout": CELERY_REDIS_SOCKET_TIMEOUT,
}
CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS = {
    "socket_connect_timeout": CELERY_REDIS_SOCKET_CONNECT_TIMEOUT,
    "socket_timeout": CELERY_REDIS_SOCKET_TIMEOUT,
    "retry_policy": {
        "max_retries": int(os.environ.get("CELERY_RESULT_BACKEND_MAX_RETRIES", "3")),
        "interval_start": 0,
        "interval_step": 0.2,
        "interval_max": 0.5,
    },
}

# Celery on Windows doesn't support the default prefork pool.
if os.name == "nt":
    CELERY_WORKER_POOL = os.environ.get("CELERY_WORKER_POOL", "solo")




# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = []
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
WHITENOISE_USE_FINDERS = DEBUG or RUNNING_TESTS
if RUNNING_TESTS:
    STATIC_ROOT.mkdir(parents=True, exist_ok=True)

if USE_S3_MEDIA:
    AWS_ACCESS_KEY_ID = env_str("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = env_str("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = env_str("AWS_STORAGE_BUCKET_NAME")
    AWS_S3_REGION_NAME = env_str("AWS_S3_REGION_NAME")
    AWS_S3_CUSTOM_DOMAIN = env_str("AWS_S3_CUSTOM_DOMAIN")
    AWS_QUERYSTRING_AUTH = env_bool("AWS_QUERYSTRING_AUTH", True)
    _media_domain = AWS_S3_CUSTOM_DOMAIN or (f"{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com" if AWS_STORAGE_BUCKET_NAME else "")
    MEDIA_URL = f"https://{_media_domain}/media/" if _media_domain else "/media/"
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {"location": "media"},
    }
else:
    MEDIA_URL = "/media/"
    MEDIA_ROOT = BASE_DIR / "media"

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
