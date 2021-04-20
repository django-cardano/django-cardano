# ------------------------------------------------------------------------------
# Django Settings
# ------------------------------------------------------------------------------
# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = False

ALLOWED_HOSTS = [
    'localhost',
]

SECRET_KEY = 'VuerXdXewc7Qyq7DzC4H8UcQUMxDCPkNU9kQnSHSLmbwb3tY49aLv6yMvT7EhB4H'

INSTALLED_APPS = [
    # Core
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sites',

    'django_cardano',
]
