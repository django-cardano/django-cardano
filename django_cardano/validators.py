from django.utils.deconstruct import deconstructible
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from django_cardano.exceptions import CardanoError
from django_cardano.util import CardanoUtils


@deconstructible
class CardanoAddressValidator:
    message = _('Enter a valid cardano address.')
    code = 'invalid'

    def __init__(self, message=None, code=None):
        if message is not None:
            self.message = message
        if code is not None:
            self.code = code

    def __call__(self, value):
        try:
            CardanoUtils.address_info(value)
        except CardanoError:
            raise ValidationError(self.message, code=self.code)

    def __eq__(self, other):
        return (
            isinstance(other, CardanoAddressValidator) and
            (self.message == other.message) and
            (self.code == other.code)
        )


validate_cardano_address = CardanoAddressValidator()
