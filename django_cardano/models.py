import io
import json
import pyAesCrypt
import tempfile
import uuid

from collections import defaultdict
from pathlib import Path
from typing import Optional

from django.db import models
from django.apps import apps as django_apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.utils.text import slugify

from .cli import (
    CardanoCLI,
    MIN_FEE_RE,
    UTXO_RE,
)

from .fields import CardanoAddressField
from .exceptions import CardanoError, CardanoErrorType
from .util import CardanoUtils

from .shortcuts import (
    filter_utxos,
    sort_utxos,
)
from .storage import CardanoDataStorage

from django_cardano.settings import (
    django_cardano_settings as cardano_settings
)

# Size of buffer supplied used for encryption of signing keys
ENCRYPTION_BUFFER_SIZE = 4 * 1024

lovelace_unit = cardano_settings.LOVELACE_UNIT


# ---------------------------------------------------------------------------------
def get_extensible_model(setting_name):
    model_name = getattr(settings, setting_name)
    try:
        return django_apps.get_model(model_name, require_ready=True)
    except ValueError:
        raise ImproperlyConfigured(f"{setting_name} must be of the form 'app_label.model_name'")
    except LookupError:
        raise ImproperlyConfigured(
            f"{setting_name} refers to model '{model_name}' that has not been installed"
        )


def get_minting_policy_model():
    """
    Return the MintingPolicy model that is active in this project.
    """
    return get_extensible_model('DJANGO_CARDANO_MINTING_POLICY_MODEL')


def get_transaction_model():
    """
    Return the Transaction model that is active in this project.
    """
    return get_extensible_model('DJANGO_CARDANO_TRANSACTION_MODEL')


def get_wallet_model():
    """
    Return the Wallet model that is active in this project.
    """
    return get_extensible_model('DJANGO_CARDANO_WALLET_MODEL')


# ------------------------------------------------------------------------------
def file_upload_path(instance, filename):
    model_name = slugify(instance._meta.verbose_name)
    return Path(model_name, str(instance.id), filename)


class MintingPolicyManager(models.Manager):
    def create(self, password, invalid_before=None, invalid_hereafter=None, **kwargs):
        policy = self.model(**kwargs)

        with tempfile.TemporaryDirectory() as tmpdirname:
            intermediate_file_path = Path(tmpdirname)

            # 1. Create signing/verification keys for the minting policy
            signing_key_path = intermediate_file_path / 'signing.key'
            verification_key_path = intermediate_file_path / 'verification.key'
            CardanoCLI.run('address key-gen', **{
                'signing-key-file': signing_key_path,
                'verification-key-file': verification_key_path,
            })

            # 2. Encrypt the generated key files and attach them to the Policy record
            for field_name, file_path in {
                'signing_key': signing_key_path,
                'verification_key': verification_key_path,
            }.items():
                with open(file_path, 'rb') as fp:
                    filename = file_path.name
                    f_ciph = io.BytesIO()
                    pyAesCrypt.encryptStream(io.BytesIO(fp.read()), f_ciph, password, ENCRYPTION_BUFFER_SIZE)
                    file_field = getattr(policy, field_name)
                    file_field.save(f'{filename}.aes', f_ciph, save=False)

            policy_key_hash = CardanoCLI.run('address key-hash', **{
                'payment-verification-key-file': verification_key_path,
            })

            # 3. Construct the policy script and attach to the Policy record
            scripts = [{
                'keyHash': policy_key_hash,
                'type': 'sig',
            }]
            if invalid_before:
                scripts.append({
                    'type': 'after',
                    'slot': invalid_before,
                })
            if invalid_hereafter:
                scripts.append({
                    'type': 'before',
                    'slot': invalid_hereafter,
                })
            policy_script_data = json.dumps({
                'type': 'all',
                'scripts': scripts
            })
            with ContentFile(policy_script_data) as file_content:
                policy.script.save('policy.script.json', file_content, save=False)

            # 4. Determine the policy ID (i.e. compute hash of the policy script)
            policy.policy_id = CardanoCLI.run('transaction policyid', **{
                'script-file': policy.script.path
            })

        policy.save(force_insert=True, using=self.db)
        return policy


class AbstractMintingPolicy(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    policy_id = models.CharField(max_length=64)

    name = models.CharField(max_length=50, blank=True)

    script = models.FileField(
        max_length=200,
        upload_to=file_upload_path,
        storage=CardanoDataStorage
    )
    signing_key = models.FileField(
        max_length=200,
        upload_to=file_upload_path,
        storage=CardanoDataStorage
    )
    verification_key = models.FileField(
        max_length=200,
        upload_to=file_upload_path,
        storage=CardanoDataStorage
    )

    objects = MintingPolicyManager()

    class Meta:
        abstract = True

    def __str__(self):
        return self.name if self.name else self.policy_id

    @property
    def script_data(self):
        if not self.script:
            return None

        with open(self.script.path, 'r') as script_file_path:
            return json.load(script_file_path)


class MintingPolicy(AbstractMintingPolicy):
    class Meta:
        swappable = 'DJANGO_CARDANO_MINTING_POLICY_MODEL'
        verbose_name_plural = 'Minting Policies'


# ---------------------------------------------------------------------------------
class TransactionTypes(models.IntegerChoices):
    LOVELACE_TRANSFER = 1
    TOKEN_TRANSFER = 2
    TOKEN_MINT = 3
    TOKEN_CONSOLIDATION = 4
    LOVELACE_PARTITION = 5


class AbstractTransaction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tx_id = models.CharField(max_length=64, blank=True, null=True)
    tx_file = models.FileField(
        max_length=200,
        upload_to=file_upload_path,
        storage=CardanoDataStorage
    )
    tx_type = models.PositiveSmallIntegerField(choices=TransactionTypes.choices)

    inputs = models.JSONField(default=list)
    outputs = models.JSONField(default=list)
    metadata = models.JSONField(blank=True, null=True)

    minting_policy = None
    minting_password = None

    class Meta:
        abstract = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.temp_directory = tempfile.TemporaryDirectory()

    def __del__(self):
        self.temp_directory.cleanup()

    def __str__(self):
        return str(self.id)

    @property
    def intermediate_file_path(self) -> Path:
        return Path(self.temp_directory.name)

    @property
    def metadata_file_path(self) -> Path:
        return self.intermediate_file_path / 'metadata.json'

    @property
    def draft_tx_file_path(self) -> Path:
        return self.intermediate_file_path / 'transaction.draft'

    @property
    def tx_info(self) -> Optional[dict]:
        return CardanoUtils.tx_info(self.tx_file.path) if self.tx_file else None

    @property
    def tx_args(self) -> list:
        return self.inputs + self.outputs

    def delete(self, using=None, keep_parents=False):
        # Destroy all intermediate files upon deletion
        self.temp_directory.cleanup()

        return super().delete(using, keep_parents)

    def generate_draft(self, **kwargs):
        cmd_kwargs = {
            **kwargs,
            'fee': 0,
            'out-file': self.draft_tx_file_path,
        }
        if self.metadata:
            with open(self.metadata_file_path, 'w') as metadata_file:
                json.dump(self.metadata, metadata_file)
            cmd_kwargs.update({
                'json-metadata-no-schema': None,
                'metadata-json-file': self.metadata_file_path,
            })
        if self.minting_policy:
            cmd_kwargs['minting-script-file'] = str(self.minting_policy.script.path)

        CardanoCLI.run('transaction build-raw', *self.tx_args, **cmd_kwargs)

    def calculate_min_fee(self) -> int:
        tx_body_file_path = Path(self.draft_tx_file_path)
        if not tx_body_file_path.exists():
            raise CardanoError('Unable to calculate minimum fee; require transaction body file.')

        CardanoUtils.refresh_protocol_parameters()

        raw_response = CardanoCLI.run('transaction calculate-min-fee', **{
            'tx-body-file': tx_body_file_path,
            'tx-in-count': len(self.inputs),
            'tx-out-count': len(self.outputs),
            'witness-count': 2 if self.minting_policy else 1,
            'byron-witness-count': 0,
            'protocol-params-file': CardanoUtils.protocol_parameters_path,
            'network': cardano_settings.NETWORK,
        })
        match = MIN_FEE_RE.match(raw_response)
        return int(match[1])

    def submit(self, wallet, fee, password, invalid_hereafter=None, **tx_kwargs):
        raw_tx_file_path = self.intermediate_file_path / 'transaction.raw'
        signed_tx_file_path = self.intermediate_file_path / 'transaction.signed'
        signing_key_file_path = self.intermediate_file_path / 'signing.key'
        policy_signing_key_file_path = self.intermediate_file_path / 'policy-signing.key'

        # Determine the TTL (time to Live) for the transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#determine-the-ttl-time-to-live-for-the-transaction
        if not invalid_hereafter:
            current_slot = int(CardanoUtils.query_tip()['slot'])
            invalid_hereafter = current_slot + cardano_settings.DEFAULT_TRANSACTION_TTL

        cmd_kwargs = {
            **tx_kwargs,
            'fee': fee,
            'invalid-hereafter': invalid_hereafter,
            'out-file': raw_tx_file_path,
        }

        if self.metadata:
            cmd_kwargs.update({
                'json-metadata-no-schema': None,
                'metadata-json-file': self.metadata_file_path,
            })

        if self.minting_policy:
            cmd_kwargs['minting-script-file'] = str(self.minting_policy.script.path)

        CardanoCLI.run('transaction build-raw', *self.tx_args, **cmd_kwargs)

        # Sign the transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#sign-the-transaction
        signing_args = []
        signing_kwargs = {
            'signing-key-file': signing_key_file_path,
            'tx-body-file': raw_tx_file_path,
            'out-file': signed_tx_file_path,
            'network': cardano_settings.NETWORK
        }

        # Decrypt this wallet's signing key and store it as a temporary file
        try:
            pyAesCrypt.decryptFile(
                wallet.payment_signing_key.path,
                signing_key_file_path,
                password,
                ENCRYPTION_BUFFER_SIZE
            )
        except ValueError as e:
            raise CardanoError(
                source_error=e,
                code=CardanoErrorType.SIGNING_KEY_DECRYPTION_FAILURE
            )

        if self.minting_policy and self.minting_password:
            # Decrypt the policy signing key and store it as a temporary file
            try:
                pyAesCrypt.decryptFile(
                    self.minting_policy.signing_key.path,
                    policy_signing_key_file_path,
                    self.minting_password,
                    ENCRYPTION_BUFFER_SIZE
                )
                signing_args.append(('signing-key-file', policy_signing_key_file_path))
            except ValueError as e:
                raise CardanoError(
                    source_error=e,
                    code=CardanoErrorType.POLICY_SIGNING_KEY_DECRYPTION_FAILURE,
                )

        # Sign the transaction
        CardanoCLI.run('transaction sign', *signing_args, **signing_kwargs)

        self.tx_id = CardanoCLI.run('transaction txid', **{
            'tx-file': signed_tx_file_path
        })

        # Submit the transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#submit-the-transaction
        CardanoCLI.run('transaction submit', **{
            'tx-file': signed_tx_file_path,
            'network': cardano_settings.NETWORK
        })

        with open(signed_tx_file_path, 'rb') as signed_tx_file:
            with ContentFile(signed_tx_file.read()) as file_content:
                self.tx_file.save(signed_tx_file_path.name, file_content, save=False)

        # Clean up intermediate files
        self.temp_directory.cleanup()


class Transaction(AbstractTransaction):
    class Meta:
        swappable = 'DJANGO_CARDANO_TRANSACTION_MODEL'


# ------------------------------------------------------------------------------
class WalletManager(models.Manager):
    use_in_migrations = True

    def create_from_path(self, path, **kwargs):
        wallet = self.model(**kwargs)

        with open(path / 'payment.addr', 'r') as payment_address_file:
            wallet.payment_address = payment_address_file.read()
        with open(path / 'staking.addr', 'r') as staking_address_file:
            wallet.stake_address = staking_address_file.read()

        # Encrypt the test key files and attach them to the wallet
        for field_name, file_path in {
            'payment_signing_key': path / 'signing.key.aes',
            'payment_verification_key': path / 'verification.key.aes',
            'stake_signing_key': path / 'stake-signing.key.aes',
            'stake_verification_key': path / 'stake-verification.key.aes',
        }.items():
            with open(file_path, 'rb') as fp:
                with ContentFile(fp.read()) as file_content:
                    field = getattr(wallet, field_name)
                    field.save(file_path.name, file_content, save=False)

        wallet.full_clean()
        wallet.save(force_insert=True, using=self.db)

        return wallet

    def create(self, password, **kwargs):
        wallet = self.model(**kwargs)

        with tempfile.TemporaryDirectory() as tmp_path:
            intermediate_file_path = Path(tmp_path)

            # Generate the payment signing & verification keys
            signing_key_path = intermediate_file_path / 'signing.key'
            verification_key_path = intermediate_file_path / 'verification.key'
            CardanoCLI.run('address key-gen', **{
                'signing-key-file': signing_key_path,
                'verification-key-file': verification_key_path,
            })

            # Generate the stake signing & verification keys
            stake_signing_key_path = intermediate_file_path / 'stake-signing.key'
            stake_verification_key_path = intermediate_file_path / 'stake-verification.key'
            CardanoCLI.run('stake-address key-gen', **{
                'signing-key-file': stake_signing_key_path,
                'verification-key-file': stake_verification_key_path,
            })

            # Create the payment address.
            wallet.payment_address = CardanoCLI.run('address build', **{
                'payment-verification-key-file': verification_key_path,
                'stake-verification-key-file': stake_verification_key_path,
                'network': cardano_settings.NETWORK,
            })

            # Create the staking address.
            wallet.stake_address = CardanoCLI.run('stake-address build', **{
                'stake-verification-key-file': stake_verification_key_path,
                'network': cardano_settings.NETWORK,
            })

            # Encrypt the generated key files and attach them to the wallet
            for field_name, file_path in {
                'payment_signing_key': signing_key_path,
                'payment_verification_key': verification_key_path,
                'stake_signing_key': stake_signing_key_path,
                'stake_verification_key': stake_verification_key_path,
            }.items():
                with open(file_path, 'rb') as fp:
                    filename = file_path.name
                    f_ciph = io.BytesIO()
                    pyAesCrypt.encryptStream(io.BytesIO(fp.read()), f_ciph, password, ENCRYPTION_BUFFER_SIZE)
                    file_field = getattr(wallet, field_name)
                    file_field.save(f'{filename}.aes', f_ciph, save=False)

        wallet.save(force_insert=True, using=self.db)
        return wallet


class AbstractWallet(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=30, blank=True)

    payment_address = CardanoAddressField()
    payment_signing_key = models.FileField(
        max_length=200,
        upload_to=file_upload_path,
        storage=CardanoDataStorage
    )
    payment_verification_key = models.FileField(
        max_length=200,
        upload_to=file_upload_path,
        storage=CardanoDataStorage
    )

    stake_address = CardanoAddressField()
    stake_signing_key = models.FileField(
        max_length=200,
        upload_to=file_upload_path,
        storage=CardanoDataStorage
    )
    stake_verification_key = models.FileField(
        max_length=200,
        upload_to=file_upload_path,
        storage=CardanoDataStorage
    )

    objects = WalletManager()

    class Meta:
        abstract = True

    def __str__(self):
        return self.name

    @property
    def utxos(self) -> list:
        utxos = []

        response = CardanoCLI.run(
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
    def lovelace_utxos(self) -> list:
        return filter_utxos(self.utxos, include=lovelace_unit)

    @property
    def token_utxos(self) -> list:
        return filter_utxos(self.utxos, exclude=lovelace_unit)

    @property
    def balance(self) -> tuple:
        utxos = self.utxos
        all_tokens = defaultdict(int)

        for utxo in utxos:
            utxo_tokens = utxo['Tokens']
            for token_id, token_count in utxo_tokens.items():
                all_tokens[token_id] += token_count

        return all_tokens, utxos

    def send_lovelace(self, quantity, to_address, password=None) -> AbstractTransaction:
        # The protocol's declared txFeeFixed will give us a fair estimate
        # of how much the fee for this transaction will be.
        protocol_parameters = CardanoUtils.refresh_protocol_parameters()
        estimated_tx_fee = protocol_parameters.get('txFeeFixed')

        transaction_class = get_transaction_model()
        transaction = transaction_class(tx_type=TransactionTypes.LOVELACE_TRANSFER)

        # Get the transaction hash and index of the UTxO(s) to spend
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#get-the-transaction-hash-and-index-of-the-utxo-to-spend
        sorted_lovelace_utxos = sort_utxos(self.lovelace_utxos)

        total_lovelace_being_sent = 0
        for utxo in sorted_lovelace_utxos:
            tx_hash = utxo['TxHash']
            tx_index = utxo['TxIx']
            transaction.inputs.append(('tx-in', f'{tx_hash}#{tx_index}'))
            total_lovelace_being_sent += utxo['Tokens'][lovelace_unit]

            # Validate whether the included UTxOs are sufficient to cover
            # the lovelace being transferred, including the estimated tx_fee.
            if total_lovelace_being_sent >= quantity + estimated_tx_fee:
                break

        # There will ALWAYS be exactly two output transactions:
        #   - The funds being sent to the recipient
        #   - The "change" being returned to the sender
        transaction.outputs = [
            ('tx-out', f'{to_address}+{quantity}'),
            ('tx-out', f'{self.payment_address}+{total_lovelace_being_sent}'),
        ]

        # Draft the transaction:
        # Produce a draft transaction in order to determine the fees required to perform the actual transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#draft-the-transaction
        transaction.generate_draft()

        # If a password was given, this implies the intention to commit the
        # transaction to the blockchain (vs. performing a dry-run)
        if password:
            # Calculate the fee
            # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-fee
            tx_fee = transaction.calculate_min_fee()

            # Calculate the change to return the payment address
            # (minus transaction fee) and update that output respectively
            # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-change-to-send-back-to-payment-addr
            lovelace_to_return = total_lovelace_being_sent - quantity - tx_fee
            transaction.outputs[-1] = ('tx-out', f'{self.payment_address}+{lovelace_to_return}')

            # Let successful transactions be persisted to the database
            transaction.submit(wallet=self, fee=tx_fee, password=password)
            transaction.save()

        return transaction

    def send_tokens(self, asset_id, quantity, to_address, password=None) -> AbstractTransaction:
        utxos = self.utxos
        sorted_lovelace_utxos = sort_utxos(self.lovelace_utxos)
        token_utxos = sort_utxos(filter_utxos(utxos, include=asset_id), type=asset_id)

        if not sorted_lovelace_utxos:
            # Let there be be at least one UTxO containing purely ADA.
            # This will be used to pay for the transaction.
            raise CardanoError('Insufficient ADA funds to complete transaction')

        transaction_model_class = get_transaction_model()
        transaction = transaction_model_class(tx_type=TransactionTypes.TOKEN_TRANSFER)

        # ASSUMPTION: The largest ADA UTxO shall contain sufficient ADA
        # to pay for the transaction (including fees)
        lovelace_utxo = sorted_lovelace_utxos[0]
        total_lovelace_being_sent = lovelace_utxo['Tokens'][lovelace_unit]
        transaction.inputs = [('tx-in', '{}#{}'.format(lovelace_utxo['TxHash'], lovelace_utxo['TxIx']))]

        # The set of transaction inputs shall be comprised of as many token UTxOs
        # as are required to accommodate the tokens_requested
        total_tokens_being_sent = 0
        for utxo in token_utxos:
            tx_hash = utxo['TxHash']
            tx_index = utxo['TxIx']
            transaction.inputs.append(('tx-in', f'{tx_hash}#{tx_index}'))
            total_tokens_being_sent += utxo['Tokens'][asset_id]

            # Accumulate the total amount of lovelace being sent
            total_lovelace_being_sent += utxo['Tokens'][lovelace_unit]

            if total_tokens_being_sent >= quantity:
                break

        if total_tokens_being_sent < quantity:
            raise CardanoError(f'Insufficient tokens. Requested: {quantity}, Available: {total_tokens_being_sent}')

        lovelace_to_return = total_lovelace_being_sent

        # Let the first transaction output represent the tokens being sent to the recipient
        token_bundle = f'"{quantity} {asset_id}"'
        token_dust = CardanoUtils.min_token_dust_value(token_bundle)
        transaction.outputs = [('tx-out', f'{to_address}+{token_dust}+{token_bundle}')]
        lovelace_to_return -= token_dust

        # If there are more tokens in this wallet than are being sent, return the rest to the sender
        tokens_to_return = total_tokens_being_sent - quantity
        if tokens_to_return > 0:
            token_bundle = f'"{tokens_to_return} {asset_id}"'
            token_dust = CardanoUtils.min_token_dust_value(token_bundle)
            transaction.outputs.append(('tx-out', f'{self.payment_address}+{token_dust}+{token_bundle}'))
            lovelace_to_return -= token_dust

        # The last output represents the lovelace being returned to the payment wallet
        transaction.outputs.append(('tx-out', f'{self.payment_address}+{lovelace_to_return}'))

        # Draft the transaction:
        # Produce a draft transaction in order to determine the fees required to perform the actual transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#draft-the-transaction
        transaction.generate_draft()

        if password:
            # Calculate the fee
            # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-fee
            tx_fee = transaction.calculate_min_fee()

            # Calculate the change to return the payment address
            # (minus transaction fee) and update that output respectively
            # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-change-to-send-back-to-payment-addr
            transaction.outputs[-1] = ('tx-out', f'{self.payment_address}+{lovelace_to_return - tx_fee}')

            transaction.submit(wallet=self, fee=tx_fee, password=password)

            # Let successful transactions be persisted to the database
            transaction.save()

        return transaction

    def consolidate_utxos(self, password=None) -> AbstractTransaction:
        all_tokens, utxos = self.balance

        transaction_model_class = get_transaction_model()
        transaction = transaction_model_class(tx_type=TransactionTypes.TOKEN_CONSOLIDATION)

        # Traverse the set of utxos at the given wallet's payment address,
        # accumulating the total count of each type of token.
        for utxo in utxos:
            tx_hash = utxo['TxHash']
            tx_index = utxo['TxIx']
            transaction.inputs.append(('tx-in', f'{tx_hash}#{tx_index}'))

        remaining_lovelace = all_tokens[lovelace_unit]
        del all_tokens[lovelace_unit]

        for asset_id, asset_count in all_tokens.items():
            # HACK!! The amount of ADA accompanying a token needs to be computed
            # with respect to that token's properties
            token_bundle = f'"{asset_count} {asset_id}"'
            token_dust = CardanoUtils.min_token_dust_value(token_bundle)
            transaction.outputs.append(('tx-out', f'{self.payment_address}+{token_dust}+{token_bundle}'))
            remaining_lovelace -= token_dust

        # This output represents the remaining ADA.
        # It must be included in draft transaction in order to accurately compute the
        # minimum transaction fee. After the minimum fee has been calculated,
        # this output will be replaced by one that accounts for that fee.
        transaction.outputs.append(('tx-out', f'{self.payment_address}+{remaining_lovelace}'))

        # Draft the transaction:
        # Produce a draft transaction in order to determine the fees required to perform the actual transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#draft-the-transaction
        transaction.generate_draft()

        if password:
            # Calculate the fee
            # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-fee
            tx_fee = transaction.calculate_min_fee()

            # Calculate the change to return the payment address
            # (minus transaction fee) and update that output respectively
            # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-change-to-send-back-to-payment-addr
            transaction.outputs[-1] = ('tx-out', f'{self.payment_address}+{remaining_lovelace - tx_fee}')

            transaction.submit(wallet=self, fee=tx_fee, password=password)

            # Let successful transactions be persisted to the database
            transaction.save()

        return transaction

    def partition_lovelace(self, values: list, password=None) -> AbstractTransaction:
        """
        Convert existing lovelace UTxOs into those of the given values.
        (Useful for testing batch creation of NFTs.)
        :param password:
        :param values:
        :return:
        """
        transaction_model_class = get_transaction_model()
        transaction = transaction_model_class(tx_type=TransactionTypes.LOVELACE_PARTITION)

        surplus_lovelace = 0
        for utxo in self.lovelace_utxos:
            tx_hash = utxo['TxHash']
            tx_index = utxo['TxIx']
            transaction.inputs.append(('tx-in', f'{tx_hash}#{tx_index}'))
            surplus_lovelace += utxo['Tokens'][lovelace_unit]

        for value in values:
            transaction.outputs.append(('tx-out', f'{self.payment_address}+{value}'))
            surplus_lovelace -= value
        # This final output transaction shall contain the surplus (minus tx fee)
        transaction.outputs.append(('tx-out', f'{self.payment_address}+{surplus_lovelace}'))

        transaction.generate_draft()

        if password is not None:
            # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-fee
            tx_fee = transaction.calculate_min_fee()

            # Calculate the change to return the payment address
            # (minus transaction fee) and update that output respectively
            # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-change-to-send-back-to-payment-addr
            transaction.outputs[-1] = ('tx-out', f'{self.payment_address}+{surplus_lovelace - tx_fee}')

            transaction.submit(wallet=self, fee=tx_fee, password=password)

            # Let successful transactions be persisted to the database
            transaction.save()

        return transaction

    def mint_tokens(self, policy, quantity, to_address, spending_password, minting_password,
                    asset_name=None, metadata=dict, payment_utxo=None, change_address=None
                    ) -> AbstractTransaction:
        """
        https://docs.cardano.org/en/latest/native-tokens/getting-started-with-native-tokens.html#start-the-minting-process
        :param policy: Policy that will be used to mint the token
        :param quantity: Number of tokens to mint
        :param to_address: Address to send minted token to
        :param spending_password: Password required to decrypt wallet signing key
        :param minting_password: Password required to decrypt policy signing key
        :param asset_name: Name component of the unique asset ID (<policy_id>.<asset_name>)
        :param metadata: Metadata to include in the minting transaction
        :param payment_utxo: Specific
        :param change_address: Address to receive excess funds
        """
        surplus_address = change_address if change_address else self.payment_address

        if not payment_utxo:
            # If a payment utxo was not explicitly provided, we will use this wallet's largest
            # UTxO with the assumption that it will cover the transaction (including fees)
            sorted_lovelace_utxos = sort_utxos(self.lovelace_utxos)
            if not sorted_lovelace_utxos:
                # Let there be be at least one UTxO containing purely ADA.
                # This will be used to pay for the transaction.
                raise CardanoError(f'Inadequate funds to complete transaction')
            payment_utxo = sorted_lovelace_utxos[0]

        # Specify the asset ID and quantity of tokens to mint
        # https://docs.cardano.org/en/latest/native-tokens/getting-started-with-native-tokens.html#syntax-of-multi-asset-values
        asset_id = policy.policy_id
        if asset_name:
            asset_id = f'{asset_id}.{asset_name}'
        token_bundle = f'"{quantity} {asset_id}"'

        # Structure the token metadata according to the proposed "721" standard
        # See: https://www.reddit.com/r/CardanoDevelopers/comments/mkhlv8/nft_metadata_standard/

        transaction_model_class = get_transaction_model()
        transaction = transaction_model_class(
            tx_type=TransactionTypes.TOKEN_MINT,
            metadata=metadata,
        )
        transaction.minting_policy = policy
        transaction.minting_password = minting_password

        # Compute the amount of lovelace required to accompany this token bundle
        token_dust = CardanoUtils.min_token_dust_value(token_bundle)
        total_lovelace_being_sent = payment_utxo['Tokens'][lovelace_unit]
        lovelace_to_return = total_lovelace_being_sent - token_dust

        transaction.inputs = [('tx-in', '{}#{}'.format(payment_utxo['TxHash'], payment_utxo['TxIx']))]
        transaction.outputs = [
            ('tx-out', f'{to_address}+{token_dust}+{token_bundle}'),
            ('tx-out', f'{surplus_address}+{lovelace_to_return}')
        ]

        # Draft the transaction:
        # Produce a draft transaction in order to determine the fees required to perform the actual transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#draft-the-transaction
        transaction.generate_draft(mint=token_bundle)

        if spending_password is not None and minting_password is not None:
            # Calculate the fee
            # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-fee
            tx_fee = transaction.calculate_min_fee()

            # Calculate the change to return the payment address
            # (minus transaction fee) and update that output respectively
            # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-change-to-send-back-to-payment-addr
            transaction.outputs[-1] = ('tx-out', f'{surplus_address}+{lovelace_to_return - tx_fee}')

            invalid_hereafter = None
            for script in policy.script_data['scripts']:
                if script['type'] == 'before':
                    invalid_hereafter = script['slot']
                    break

            transaction.submit(
                wallet=self,
                fee=tx_fee,
                password=spending_password,
                mint=token_bundle,
                invalid_hereafter=invalid_hereafter
            )

            # Let successful transactions be persisted to the database
            transaction.save()

        return transaction


class Wallet(AbstractWallet):
    class Meta(AbstractWallet.Meta):
        swappable = 'DJANGO_CARDANO_WALLET_MODEL'
