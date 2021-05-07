from django.db.models import CharField
from django.utils.translation import gettext_lazy as _

from . import validators

__all__ = [
    'CardanoAddressField',
]


class CardanoAddressField(CharField):
    default_validators = [validators.validate_cardano_address]
    description = _("Cardano address")

    def __init__(self, *args, **kwargs):
        # TODO: Determine what the actual max length of a cardano address may be
        kwargs.setdefault('max_length', 200)
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        # We do not exclude max_length if it matches default as we want to change
        # the default in future.
        return name, path, args, kwargs
