import json
import os
import shutil
import uuid
from collections import defaultdict

from django.db import models
from django_cryptography.fields import encrypt

from .cli import (
    CardanoCLI,
    UTXO_RE,
)

from .shortcuts import (
    create_intermediate_directory,
)

from django_cardano.settings import (
    django_cardano_settings as cardano_settings
)


# Output of 'query utxo' command is presumed to yield an ASCII table
# containing rows of the form: <TxHash>    <TxIx>      <Amount>
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

        intermediate_file_path = create_intermediate_directory('wallet', str(wallet.id))
        os.makedirs(intermediate_file_path, 0o755)

        # Generate the payment signing & verification keys
        signing_key_path = os.path.join(intermediate_file_path, 'signing.key')
        verification_key_path = os.path.join(intermediate_file_path, 'verification.key')

        self.cli.run('address key-gen', **{
            'signing-key-file': signing_key_path,
            'verification-key-file': verification_key_path,
        })

        # Generate the stake signing & verification keys
        stake_signing_key_path = os.path.join(intermediate_file_path, 'stake_signing.key')
        stake_verification_key_path = os.path.join(intermediate_file_path, 'stake_verification.key')

        self.cli.run('stake-address key-gen', **{
            'signing-key-file': stake_signing_key_path,
            'verification-key-file': stake_verification_key_path,
        })

        # Create the payment address.
        wallet.payment_address = self.cli.run('address build', **{
            'payment-verification-key-file': verification_key_path,
            'stake-verification-key-file': stake_verification_key_path,
            'network': cardano_settings.NETWORK,
        })

        # Create the staking address.
        wallet.stake_address = self.cli.run('stake-address build', **{
            'stake-verification-key-file': stake_verification_key_path,
            'network': cardano_settings.NETWORK,
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cli = CardanoCLI()

    @property
    def payment_address_info(self):
        response = self.cli.run('address info', address=self.payment_address)
        return json.loads(response)

    @property
    def utxos(self) -> list:
        utxos = []

        response = self.cli.run(
            'query utxo',
            address=self.payment_address,
            network=cardano_settings.NETWORK
        )

        lines = response.split('\n')
        for line in lines[2:]:
            match = UTXO_RE.match(line)
            utxo_info = {
                'TxHash': match[1],
                'TxIx': match[2],
                'Tokens': {},
            }

            tokens = match[3].split('+')
            for token in tokens:
                token_info = token.split()
                asset_count = int(token_info[0])
                asset_type = token_info[1]
                utxo_info['Tokens'][asset_type] = asset_count
            utxos.append(utxo_info)

        return utxos

    @property
    def balance(self) -> tuple:
        utxos = self.utxos

        all_tokens = defaultdict(int)
        for utxo in utxos:
            utxo_tokens = utxo['Tokens']
            for token_id, token_count in utxo_tokens.items():
                all_tokens[token_id] += token_count

        return all_tokens, utxos


class TransactionManager(models.Manager):
    pass


class Transaction(models.Model):
    raw = models.JSONField()
    signed = models.JSONField()


class MintingPolicyManager(models.Manager):
    def create(self, **kwargs):
        policy = self.model(**kwargs)

        cardano_cli = CardanoCLI()

        intermediate_file_path = create_intermediate_directory('policy', str(policy.id))
        os.makedirs(intermediate_file_path, 0o755)

        # 1. Create a minting policy
        policy_signing_key_path = os.path.join(intermediate_file_path, 'policy.skey')
        policy_verification_key_path = os.path.join(intermediate_file_path, 'policy.vkey')
        policy_script_path = os.path.join(intermediate_file_path, 'policy.script')
        self.cli.run('address key-gen', **{
            'signing-key-file': policy_signing_key_path,
            'verification-key-file': policy_verification_key_path,
        })
        policy_key_hash = self.cli.run('address key-hash', **{
            'payment-verification-key-file': policy_verification_key_path,
        })
        policy_info = {'keyHash': policy_key_hash, 'type': 'sig'}

        with open(policy_script_path, 'w') as policy_script_file:
            json.dump(policy_info, policy_script_file)
        policy.policy_id = self.cli.run('transaction policyid', **{
            'script-file': policy_script_path
        })

        policy.save(force_insert=True, using=self.db)

        shutil.rmtree(intermediate_file_path)

        return policy


class MintingPolicy(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    policy_id = models.CharField(max_length=64)

    objects = MintingPolicyManager()

    def __str__(self):
        return self.policy_id
