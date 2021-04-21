import json
import uuid

from django.db import models
from django_cryptography.fields import encrypt


class WalletManager(models.Manager):
    def create_from_path(self, path):
        wallet = self.model()

        with open(path / 'signing.key', 'r') as signing_key_file:
            wallet.payment_signing_key = json.load(signing_key_file)
        with open(path / 'verification.key', 'r') as verification_key_file:
            wallet.payment_verification_key = json.load(verification_key_file)

        with open(path / 'stake_signing.key', 'r') as stake_signing_key_file:
            wallet.stake_signing_key = json.load(stake_signing_key_file)
        with open(path / 'stake_verification.key', 'r') as stake_verification_key_file:
            wallet.stake_verification_key = json.load(stake_verification_key_file)

        with open(path / 'payment.addr', 'r') as payment_address_file:
            wallet.payment_address = payment_address_file.read()
        with open(path / 'staking.addr', 'r') as staking_address_file:
            wallet.stake_address = staking_address_file.read()

        wallet.save()
        return wallet

class Wallet(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=30)

    payment_address = models.CharField(max_length=128)
    payment_signing_key = encrypt(models.JSONField())
    payment_verification_key = encrypt(models.JSONField())

    stake_address = models.CharField(max_length=128)
    stake_signing_key = encrypt(models.JSONField())
    stake_verification_key = encrypt(models.JSONField())

    objects = WalletManager()

    def __str__(self):
        return self.payment_address


