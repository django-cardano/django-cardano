import json
import os
import shutil
import uuid
from collections import defaultdict

from django.db import models

from django_cryptography.fields import encrypt

from .cli import (
    CardanoCLI,
    MIN_FEE_RE,
    UTXO_RE,
)
from .exceptions import CardanoError
from .util import CardanoUtils

from .shortcuts import (
    create_intermediate_directory,
    filter_utxos,
    sort_utxos,
)

from django_cardano.settings import (
    django_cardano_settings as cardano_settings
)


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
        cardano_cli.run('address key-gen', **{
            'signing-key-file': policy_signing_key_path,
            'verification-key-file': policy_verification_key_path,
        })
        policy_key_hash = cardano_cli.run('address key-hash', **{
            'payment-verification-key-file': policy_verification_key_path,
        })
        policy_info = {'keyHash': policy_key_hash, 'type': 'sig'}

        with open(policy_script_path, 'w') as policy_script_file:
            json.dump(policy_info, policy_script_file)
        policy.policy_id = cardano_cli.run('transaction policyid', **{
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


# ------------------------------------------------------------------------------
class TransactionManager(models.Manager):
    pass


class TransactionTypes(models.IntegerChoices):
    LOVELACE_PAYMENT = 1
    TOKEN_PAYMENT = 2
    TOKEN_MINT = 3
    TOKEN_CONSOLIDATION = 4


class Transaction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    date_created = models.DateTimeField(auto_now_add=True, editable=False)

    signed = models.JSONField(blank=True, null=True)
    type = models.PositiveSmallIntegerField(choices=TransactionTypes.choices)

    minting_policy = models.OneToOneField(MintingPolicy, blank=True, null=True, on_delete=models.PROTECT)
    wallet = models.ForeignKey('Wallet', on_delete=models.PROTECT)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.cli = CardanoCLI()
        self.cardano_utils = CardanoUtils()
        self.inputs = []
        self.outputs = []

    @property
    def intermediate_file_path(self):
        return os.path.join(
            cardano_settings.INTERMEDIATE_FILE_PATH,
            'tx',
            str(self.date_created.timestamp()),
        )

    @property
    def tx_args(self):
        return self.inputs + self.outputs

    @property
    def draft_tx_file_path(self):
        return os.path.join(self.intermediate_file_path, 'transaction.draft')

    @property
    def raw_tx_file_path(self):
        return os.path.join(self.intermediate_file_path, 'transaction.raw')

    @property
    def signed_tx_file_path(self):
        return os.path.join(self.intermediate_file_path, 'transaction.signed')

    @property
    def signing_key_file_path(self):
        return os.path.join(self.intermediate_file_path, 'transaction.skey')

    def generate_draft(self):
        os.makedirs(self.intermediate_file_path, 0o755, exist_ok=True)
        self.cli.run('transaction build-raw', *self.tx_args, **{
            'fee': 0,
            'out-file': self.draft_tx_file_path
        })

    def calculate_min_fee(self):
        if not os.path.exists(self.draft_tx_file_path):
            raise CardanoError('Unable to calculate minimum fee; require draft transaction.')

        self.cardano_utils.refresh_protocol_parameters()

        raw_response = self.cli.run('transaction calculate-min-fee', **{
            'tx-body-file': self.draft_tx_file_path,
            'tx-in-count': len(self.inputs),
            'tx-out-count': len(self.outputs),
            'witness-count': 1,
            'byron-witness-count': 0,
            'protocol-params-file': self.cardano_utils.protocol_parameters_path,
            'network': cardano_settings.NETWORK,
        })
        match = MIN_FEE_RE.match(raw_response)
        return int(match[1])

    def submit(self, fee):
        os.makedirs(self.intermediate_file_path, 0o755, exist_ok=True)

        # Determine the TTL (time to Live) for the transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#determine-the-ttl-time-to-live-for-the-transaction
        tip = self.cardano_utils.query_tip()
        current_slot = int(tip['slot'])
        invalid_hereafter = current_slot + cardano_settings.DEFAULT_TRANSACTION_TTL

        raw_tx_args = {
            'fee': fee,
            'invalid-hereafter': invalid_hereafter,
            'out-file': self.raw_tx_file_path,
        }

        self.cli.run('transaction build-raw', *self.tx_args, **raw_tx_args)

        # Sign the transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#sign-the-transaction
        signing_args = []
        signing_kwargs = {
            'tx-body-file': self.raw_tx_file_path,
            'out-file': self.signed_tx_file_path,
            'network': cardano_settings.NETWORK
        }

        with open(self.signing_key_file_path, 'w') as signing_key_file:
            json.dump(self.wallet.payment_signing_key, signing_key_file)
        signing_args.append(('signing-key-file', self.signing_key_file_path))

        # if 'mint' in tx_kwargs:
        #     policy_signing_key_path = os.path.join(tx_file_directory, 'policy.skey')
        #     signing_args.append(('signing-key-file', policy_signing_key_path))
        #
        #     policy_script_path = os.path.join(tx_file_directory, 'policy.script')
        #     signing_kwargs['script-file'] = policy_script_path

        self.cli.run('transaction sign', *signing_args, **signing_kwargs)
        with open(self.signed_tx_file_path, 'r') as signed_tx_file:
            self.signed_tx = json.load(signed_tx_file)
            self.save()

        # Submit the transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#submit-the-transaction
        self.cli.run('transaction submit', **{
            'tx-file': self.signed_tx_file_path,
            'network': cardano_settings.NETWORK
        })

        # Clean up intermediate files
        shutil.rmtree(self.intermediate_file_path)

# ------------------------------------------------------------------------------
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cli = CardanoCLI()
        self.cardano_utils = CardanoUtils()

    def __str__(self):
        return self.payment_address

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

    def send_lovelace(self, lovelace_requested, to_address) -> None:
        lovelace_unit = cardano_settings.LOVELACE_UNIT
        from_address = self.payment_address
        utxos = self.utxos

        transaction = Transaction.objects.create(
            type=TransactionTypes.LOVELACE_PAYMENT,
            wallet=self
        )

        # Get protocol parameters
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#get-protocol-parameters
        # ALWAYS work with a fresh set of protocol parameters.
        self.cardano_utils.refresh_protocol_parameters()

        # Get the transaction hash and index of the UTXO to spend
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#get-the-transaction-hash-and-index-of-the-utxo-to-spend
        #
        # In an effort to keep the wallet UTxOs tidy, the idea here is to
        # exhaust all of the smallest UTxOs before moving on to the bigger ones.
        # Think of it like money: normally you'd spend your change and small
        # bills before breaking out the benjies.
        sorted_lovelace_utxos = sort_utxos(
            filter_utxos(utxos, type=lovelace_unit),
            type=lovelace_unit,
        )

        total_lovelace_being_sent = 0
        for utxo in sorted_lovelace_utxos:
            tx_hash = utxo['TxHash']
            tx_index = utxo['TxIx']
            transaction.inputs.append(('tx-in', f'{tx_hash}#{tx_index}'))
            total_lovelace_being_sent += utxo['Tokens'][lovelace_unit]
            if total_lovelace_being_sent >= lovelace_requested:
                break

        # There will ALWAYS be exactly two output transactions:
        #   - The funds being sent to the recipient
        #   - The "change" being returned to the sender
        transaction.outputs = [
            ('tx-out', f'{to_address}+{lovelace_requested}'),
            ('tx-out', f'{from_address}+{total_lovelace_being_sent}'),
        ]

        # Draft the transaction:
        # Produce a draft transaction in order to determine the fees required to perform the actual transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#draft-the-transaction
        transaction.generate_draft()

        # Calculate the fee
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-fee
        tx_fee = transaction.calculate_min_fee()

        # Calculate the change to return the payment address
        # (minus transacction fee) and update that output respectively
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-change-to-send-back-to-payment-addr
        lovelace_to_return = total_lovelace_being_sent - lovelace_requested - tx_fee
        transaction.outputs[-1] = ('tx-out', f'{from_address}+{lovelace_to_return}')

        transaction.submit(fee=tx_fee)

    def send_tokens(self, token_quantity, token_id, to_address) -> None:
        # ALWAYS work with a fresh set of protocol parameters.
        protocol_parameters = self.cardano_utils.refresh_protocol_parameters()

        lovelace_unit = cardano_settings.LOVELACE_UNIT
        min_utxo_value = protocol_parameters['minUTxOValue']
        payment_address = self.payment_address
        token_lovelace = min_utxo_value * 2

        utxos = self.utxos
        lovelace_utxos = sort_utxos(filter_utxos(utxos, type=lovelace_unit), order='desc')
        token_utxos = sort_utxos(filter_utxos(utxos, type=token_id), type=token_id)

        if not lovelace_utxos:
            # Let there be be at least one UTxO containing purely ADA.
            # This will be used to pay for the transaction.
            raise CardanoError('Insufficient ADA funds to complete transaction')

        transaction = Transaction.objects.create(
            type=TransactionTypes.TOKEN_PAYMENT,
            wallet=self
        )

        # ASSUMPTION: The largest ADA UTxO shall contain sufficient ADA
        # to pay for the transaction (including fees)
        lovelace_utxo = lovelace_utxos[0]
        total_lovelace_being_sent = lovelace_utxo['Tokens'][lovelace_unit]
        transaction.inputs = [('tx-in', '{}#{}'.format(lovelace_utxo['TxHash'], lovelace_utxo['TxIx']))]

        # The set of transaction inputs shall be comprised of as many token UTxOs
        # as are required to accommodate the tokens_requested
        total_tokens_being_sent = 0
        for utxo in token_utxos:
            tx_hash = utxo['TxHash']
            tx_index = utxo['TxIx']
            transaction.inputs.append(('tx-in', f'{tx_hash}#{tx_index}'))
            total_tokens_being_sent += utxo['Tokens'][token_id]

            # Accumulate the total amount of lovelace being sent
            total_lovelace_being_sent += utxo['Tokens'][lovelace_unit]

            if total_tokens_being_sent >= token_quantity:
                break

        if total_tokens_being_sent < token_quantity:
            raise CardanoError(f'Insufficient tokens. Requested: {token_quantity}, Available: {total_tokens_being_sent}')

        lovelace_to_return = total_lovelace_being_sent

        # Let the first transaction output represent the tokens being sent to the recipient
        transaction.outputs = [('tx-out', f'{to_address}+{token_lovelace}+"{token_quantity} {token_id}"')]
        lovelace_to_return -= token_lovelace

        # If there are more tokens in this wallet than are being sent, return the rest to the sender
        tokens_to_return = total_tokens_being_sent - token_quantity
        if tokens_to_return > 0:
            transaction.outputs.append(('tx-out', f'{payment_address}+{token_lovelace}+"{tokens_to_return} {token_id}"'))
            lovelace_to_return -= token_lovelace

        # The last output represents the lovelace being returned to the payment wallet
        transaction.outputs.append(('tx-out', f'{payment_address}+{lovelace_to_return}'))

        # Draft the transaction:
        # Produce a draft transaction in order to determine the fees required to perform the actual transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#draft-the-transaction
        transaction.generate_draft()

        # Calculate the fee
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-fee
        tx_fee = transaction.calculate_min_fee()

        # Calculate the change to return the payment address
        # (minus transacction fee) and update that output respectively
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-change-to-send-back-to-payment-addr
        transaction.outputs[-1] = ('tx-out', f'{payment_address}+{lovelace_to_return - tx_fee}')

        transaction.submit(fee=tx_fee)

        # tx_args = tx_in_list + tx_out_list
        # draft_transaction_path = os.path.join(tx_file_directory, 'transaction.draft')
        # self.cli.run('transaction build-raw', *tx_args, **{
        #     'fee': 0,
        #     'out-file': draft_transaction_path
        # })
        #
        # # 3.4. Calculate the fee
        # # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-fee
        # tx_fee = self.calculate_min_fee(**{
        #     'tx-body-file': draft_transaction_path,
        #     'tx-in-count': len(tx_in_list),
        #     'tx-out-count': len(tx_out_list),
        #     'witness-count': 1,
        #     'byron-witness-count': 0,
        # })
        #
        # # 3.5. Calculate the change to send back to payment.addr and update that output respectively
        # # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-change-to-send-back-to-payment-addr
        # tx_args[len(tx_args) - 1] = ('tx-out', f'{payment_address}+{lovelace_to_return - tx_fee}')
        #
        # self._submit_transaction(tx_file_directory, from_wallet, *tx_args, fee=tx_fee)