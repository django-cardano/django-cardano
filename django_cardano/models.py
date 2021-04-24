import json
import os
import shutil
import uuid

from django.db import models
from django_cryptography.fields import encrypt

from django_cardano.cli import CardanoCLI
from django_cardano.settings import django_cardano_settings


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

        wallet.save(force_insert=True, using=self.db)

        return wallet

    def create(self, **kwargs):
        wallet = self.model(**kwargs)

        cardano_cli = CardanoCLI()

        intermediate_file_path = os.path.join(django_cardano_settings.INTERMEDIATE_FILE_PATH, 'wallet', str(wallet.id))
        os.makedirs(intermediate_file_path, 0o755)

        # Generate the payment signing & verification keys
        signing_key_path = os.path.join(intermediate_file_path, 'signing.key')
        verification_key_path = os.path.join(intermediate_file_path, 'verification.key')

        cardano_cli.run('address key-gen', **{
            'signing-key-file': signing_key_path,
            'verification-key-file': verification_key_path,
        })

        # Generate the stake signing & verification keys
        stake_signing_key_path = os.path.join(intermediate_file_path, 'stake_signing.key')
        stake_verification_key_path = os.path.join(intermediate_file_path, 'stake_verification.key')

        cardano_cli.run('stake-address key-gen', **{
            'signing-key-file': stake_signing_key_path,
            'verification-key-file': stake_verification_key_path,
        })

        # Create the payment address.
        wallet.payment_address = cardano_cli.run('address build', **{
            'payment-verification-key-file': verification_key_path,
            'stake-verification-key-file': stake_verification_key_path,
            'network': django_cardano_settings.NETWORK,
        })

        # Create the staking address.
        wallet.stake_address = cardano_cli.run('stake-address build', **{
            'stake-verification-key-file': stake_verification_key_path,
            'network': django_cardano_settings.NETWORK,
        })

        # Attach the generated key files to the wallet
        # (Note: their stored values will be encrypted)
        with open(signing_key_path, 'r') as signing_key_file:
            wallet.payment_signing_key = json.load(signing_key_file)
        with open(verification_key_path, 'r') as verification_key_file:
            wallet.payment_verification_key = json.load(verification_key_file)

        with open(stake_signing_key_path, 'r') as stake_signing_key_file:
            wallet.stake_signing_key = json.load(stake_signing_key_file)
        with open(stake_verification_key_path, 'r') as stake_verification_key_file:
            wallet.stake_verification_key = json.load(stake_verification_key_file)

        wallet.save(force_insert=True, using=self.db)

        shutil.rmtree(intermediate_file_path)

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


