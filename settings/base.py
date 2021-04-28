import os
from pathlib import Path

DEBUG = True

ALLOWED_HOSTS = []
ENVIRONMENT = os.environ.get('DJANGO_ENV', 'production')
PROJECT_PATH = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = '2ox@%(q%va#li3__mv7-y^(+w_(_$-0cr^amsrj^n_43z*4t$t'

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
DJANGO_CARDANO = {
    'DEFAULT_DUST': 2000000,
    'CLI_PATH': '/path/to/cardano-cli',
    'INTERMEDIATE_FILE_PATH': '/writable/path/for/intermediate/files',
    'LOVELACE_UNIT': 'lovelace',
    'NODE_SOCKET_PATH': '/path/to/cardano/node.socket',
    'NETWORK': 'mainnet',
    'TESTNET_MAGIC': '1097911063',
}
DJANGO_CARDANO_WALLET_MODEL = 'django_cardano.Wallet'