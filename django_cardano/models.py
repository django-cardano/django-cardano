import uuid

from django.db import models
from django_cryptography.fields import encrypt


class Wallet(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=30)

    payment_address = models.CharField(max_length=128)
    payment_signing_key = encrypt(models.JSONField())
    payment_verification_key = encrypt(models.JSONField())

    stake_address = models.CharField(max_length=128)
    stake_signing_key = encrypt(models.JSONField())
    stake_verification_key = encrypt(models.JSONField())

    def __str__(self):
        return self.payment_address


