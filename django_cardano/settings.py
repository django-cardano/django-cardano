import os

from django.conf import settings
from django.test.signals import setting_changed
from django.utils.module_loading import import_string

USER_SETTINGS = getattr(settings, 'DJANGO_CARDANO', None)

DEFAULTS = {
    'APP_DATA_PATH': os.environ.get('CARDANO_APP_DATA_PATH'),
    'CLI_PATH': os.environ.get('CARDANO_CLI_PATH'),
    'DEFAULT_TRANSACTION_TTL': 1000,
    'LOVELACE_UNIT': 'lovelace',
    'NETWORK': 'mainnet',
    'NODE_SOCKET_PATH': os.environ.get('CARDANO_NODE_SOCKET_PATH'),
    'PROTOCOL_TTL': 3600,
    # Magic numbers
    'TESTNET_MAGIC': 1097911063,
    'COIN_SIZE': 0,
    'UTXO_ENTRY_SIZE_WITHOUT_VAL': 27,
}

IMPORT_STRINGS = (

)

REMOVED_SETTINGS = (
    'SECRET_KEY',
)


def import_from_string(val, setting_name):
    """
    Attempt to import a class from a string representation.
    """
    try:
        return import_string(val)
    except ImportError as e:
        msg = "Could not import '%s' for DJANGO_CARDANO setting '%s'. %s: %s." % (val, setting_name, e.__class__.__name__, e)
        raise ImportError(msg)


def perform_import(val, setting_name):
    """
    If the given setting is a string import notation,
    then perform the necessary import or imports.
    """
    if val is None:
        return None
    elif isinstance(val, str):
        return import_from_string(val, setting_name)
    elif isinstance(val, (list, tuple)):
        return [import_from_string(item, setting_name) for item in val]
    return val


class DjangoCardanoSettings:
    """
    A settings object that allows REST Framework settings to be accessed as
    properties. For example:

        from rest_framework.settings import api_settings
        print(api_settings.DEFAULT_RENDERER_CLASSES)

    Any setting with string import paths will be automatically resolved
    and return the class, rather than the string literal.

    Note:
    This is an internal class that is only compatible with settings namespaced
    under the DJANGO_CARDANO name. It is not intended to be used by 3rd-party
    apps, and test helpers like `override_settings` may not work as expected.
    """
    def __init__(self, user_settings=None, defaults=None, import_strings=None):
        if user_settings:
            self._user_settings = self.__check_user_settings(user_settings)
        self.defaults = defaults or DEFAULTS
        self.import_strings = import_strings or IMPORT_STRINGS
        self._cached_attrs = set()

    @property
    def user_settings(self):
        if not hasattr(self, '_user_settings'):
            self._user_settings = getattr(settings, 'DJANGO_CARDANO', {})
        return self._user_settings

    def __getattr__(self, attr):
        if attr not in self.defaults:
            raise AttributeError("Invalid application setting: '%s'" % attr)

        try:
            # Check if present in user settings
            val = self.user_settings[attr]
        except KeyError:
            # Fall back to defaults
            val = self.defaults[attr]

        # Coerce import strings into classes
        if attr in self.import_strings:
            val = perform_import(val, attr)

        # Cache the result
        self._cached_attrs.add(attr)
        setattr(self, attr, val)
        return val

    @staticmethod
    def __check_user_settings(user_settings):
        for setting in REMOVED_SETTINGS:
            if setting in user_settings:
                raise RuntimeError("The '{}' setting has been removed.".format(setting))
        return user_settings

    def reload(self):
        for attr in self._cached_attrs:
            delattr(self, attr)
        self._cached_attrs.clear()
        if hasattr(self, '_user_settings'):
            delattr(self, '_user_settings')


django_cardano_settings = DjangoCardanoSettings(USER_SETTINGS, DEFAULTS, IMPORT_STRINGS)


# -----------------------------------------------------------------------------
def reload_settings(*args, **kwargs):  # pragma: no cover
    global django_cardano_settings

    setting, value = kwargs['setting'], kwargs['value']

    if setting == 'DJANGO_CARDANO':
        django_cardano_settings = DjangoCardanoSettings(value, DEFAULTS, IMPORT_STRINGS)


setting_changed.connect(reload_settings)
