"""
Django settings for the NABISWA WIFI billing platform.

Design notes:
- No SQLite, ever. DATABASE_URL always points at Supabase Postgres.
- No reliance on local filesystem for persistent data (media should move to
  Supabase Storage / S3-compatible storage once needed; not required for Phase 1).
- Everything sensitive comes from environment variables (python-decouple).
"""
import os
from pathlib import Path
from decouple import config, Csv
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('DJANGO_SECRET_KEY', default='insecure-dev-key-change-me')
DEBUG = config('DJANGO_DEBUG', default=False, cast=bool)
ALLOWED_HOSTS =['*']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'rest_framework',
    'corsheaders',

    # Project apps
    'apps.core',
    'apps.accounts',
    'apps.customers',
    'apps.packages',
    'apps.billing',
    'apps.mikrotik',
    'apps.vouchers',
]

AUTH_USER_MODEL = 'accounts.User'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'apps.core.middleware.AuditRequestMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.core.context_processors.branding',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# --- Database: Supabase Postgres only, never SQLite ---
DATABASES = {
    'default': dj_database_url.config(
        default=config('DATABASE_URL'),
        conn_max_age=0,          # serverless: don't hold connections open between invocations
        ssl_require=True,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = config('DEFAULT_TIMEZONE', default='Africa/Nairobi')
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.TokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.ScopedRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'mpesa-stkpush': '10/min',
        'mpesa-callback': '60/min',
    },
}

CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', default='', cast=Csv())

# --- M-Pesa Daraja (never referenced from templates/JS) ---
MPESA_ENV = config('MPESA_ENV', default='sandbox')
MPESA_CONSUMER_KEY = config('MPESA_CONSUMER_KEY', default='')
MPESA_CONSUMER_SECRET = config('MPESA_CONSUMER_SECRET', default='')

# These are NOT the same number for a Buy Goods Till:
#   MPESA_SHORTCODE   -> BusinessShortCode: the HO/store number registered
#                        on Daraja (what the API authenticates against).
#   MPESA_TILL_NUMBER -> PartyB: the actual till number customers dial/select
#                        via SIM Toolkit or M-PESA app to pay. For a Paybill
#                        account these are usually the same number, so
#                        MPESA_TILL_NUMBER can be left blank and PartyB
#                        falls back to MPESA_SHORTCODE (see below).
MPESA_SHORTCODE = config('MPESA_SHORTCODE', default='')
MPESA_TILL_NUMBER = config('MPESA_TILL_NUMBER', default='')
MPESA_PASSKEY = config('MPESA_PASSKEY', default='')
MPESA_CALLBACK_URL = config('MPESA_CALLBACK_URL', default='')

# NABISWA WIFI takes payments on a Till (Buy Goods), not a Paybill — this
# decides the STK push "TransactionType" in Phase 3:
#   TILL    -> CustomerBuyGoodsOnline  (PartyB = MPESA_TILL_NUMBER)
#   PAYBILL -> CustomerPayBillOnline   (PartyB = MPESA_SHORTCODE)
MPESA_ACCOUNT_TYPE = config('MPESA_ACCOUNT_TYPE', default='TILL')  # 'TILL' or 'PAYBILL'
MPESA_TRANSACTION_TYPE = (
    'CustomerBuyGoodsOnline' if MPESA_ACCOUNT_TYPE == 'TILL' else 'CustomerPayBillOnline'
)
# The actual PartyB to send in the STK push payload — resolved once here
# so Phase 3's request-building code just reads settings.MPESA_PARTY_B.
MPESA_PARTY_B = MPESA_TILL_NUMBER if MPESA_ACCOUNT_TYPE == 'TILL' and MPESA_TILL_NUMBER else MPESA_SHORTCODE

# --- Internal worker auth (for the separate expiry/MikroTik-sync process) ---
INTERNAL_TASK_TOKEN = config('INTERNAL_TASK_TOKEN', default='')

LOGIN_URL = '/admin-portal/login/'
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = 'DENY'
