import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_PATH = Path(__file__).resolve().parent
DATA_PATH = PROJECT_PATH / 'data'
APP_DATA_PATH = DATA_PATH / 'app'

load_dotenv(PROJECT_PATH / '.env')

DEBUG = int(os.environ.get("DEBUG", default=0))
SECRET_KEY = os.environ.get("SECRET_KEY")

# 'DJANGO_ALLOWED_HOSTS' should be a single string of hosts with a space between each.
# For example: 'DJANGO_ALLOWED_HOSTS=localhost 127.0.0.1 [::1]'
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS").split(" ")



INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django_cardano',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.common.CommonMiddleware',
]

ROOT_URLCONF = 'urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]


# Database
# https://docs.djangoproject.com/en/3.1/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': PROJECT_PATH / 'db.sqlite3',
    },
}

# Internationalization
# https://docs.djangoproject.com/en/3.1/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_L10N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/3.1/howto/static-files/

STATIC_URL = '/static/'

# ------------------------------------------------------------------------------
DJANGO_CARDANO_WALLET_MODEL = 'django_cardano.Wallet'
DJANGO_CARDANO_MINTING_POLICY_MODEL = 'django_cardano.MintingPolicy'
