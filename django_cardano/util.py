import json
import os
import shutil

from .settings import django_cardano_settings as settings
from .shortcuts import (
    create_intermediate_directory,
    filter_utxos,
    sort_utxos,
)

from .cli import (
    CardanoCLI,
    MIN_FEE_RE,
    UTXO_RE,
)

from .exceptions import CardanoError


class CardanoUtils:
    def __init__(self) -> None:
        super().__init__()

        self.cli = CardanoCLI()
        
        if not os.path.exists(settings.INTERMEDIATE_FILE_PATH):
            os.makedirs(settings.INTERMEDIATE_FILE_PATH, 0o755)

        self.protocol_parameters_path = os.path.join(settings.INTERMEDIATE_FILE_PATH, 'protocol.json')

    def refresh_protocol_parameters(self) -> dict:
        protocol_params_raw = self.cli.run('query protocol-parameters', network=settings.NETWORK)
        protocol_parameters = json.loads(protocol_params_raw)
        with open(self.protocol_parameters_path, 'w') as protocol_parameters_file:
            json.dump(protocol_parameters, protocol_parameters_file)

        return protocol_parameters

    def query_tip(self) -> dict:
        response = self.cli.run('query tip', network=settings.NETWORK)
        return json.loads(response)

    def consolidate_tokens(self, wallet) -> None:
        tx_file_directory = create_intermediate_directory('tx')

        protocol_parameters = self.refresh_protocol_parameters()
        min_utxo_value = protocol_parameters['minUTxOValue']

        # HACK!! How do we compute the actual amount of lovelace that
        # is required to be attached to a token??
        token_lovelace = min_utxo_value * 2

        payment_address = wallet.payment_address
        all_tokens, utxos = wallet.balance

        # Traverse the set of utxos at the given wallet's payment address,
        # accumulating the total count of each type of token.
        tx_in_list = []
        for utxo in utxos:
            tx_hash = utxo['TxHash']
            tx_index = utxo['TxIx']
            tx_in_list.append(('tx-in', f'{tx_hash}#{tx_index}'))

        remaining_lovelace = all_tokens[settings.LOVELACE_UNIT]
        del all_tokens[settings.LOVELACE_UNIT]

        tx_out_list = []
        for asset_id, asset_count in all_tokens.items():
            tx_out_list.append(('tx-out', f'{payment_address}+{token_lovelace}+"{asset_count} {asset_id}"'))
            remaining_lovelace -= token_lovelace

        # This output represents the remaining ADA.
        # It must be included in draft transaction in order to accurately compute the
        # minimum transaction fee. After the minimum fee has been calculated,
        # this output will be replaced by one that accounts for that fee.
        tx_out_list.append(('tx-out', f'{payment_address}+{remaining_lovelace}'))

        # Create a draft transaction used to calculate the minimum transaction fee
        tx_args = tx_in_list + tx_out_list
        draft_transaction_path = os.path.join(tx_file_directory, 'transaction.draft')
        self.cli.run('transaction build-raw', *tx_args, **{
            'invalid-hereafter': 0,
            'fee': 0,
            'out-file': draft_transaction_path
        })

        tx_fee = self.calculate_min_fee(**{
            'tx-body-file': draft_transaction_path,
            'tx-in-count': len(tx_in_list),
            'tx-out-count': len(tx_out_list),
            'witness-count': 1,
            'byron-witness-count': 0,
        })

        if remaining_lovelace - tx_fee < min_utxo_value:
            # Now that the transaction fee has been calculated, ensure there is
            # enough lovelace leftover to produce the output containing the remaining ADA
            raise CardanoError('Insufficient lovelace available to perform consolidation.')

        # Update the "remainder" output with the balance minus for the transaction fee
        tx_args[len(tx_args) - 1] = ('tx-out', f'{payment_address}+{remaining_lovelace - tx_fee}')

        self._submit_transaction(tx_file_directory, wallet, *tx_args, fee=tx_fee)


    def mint_nft(self, asset_name, metadata, from_wallet) -> None:
        """
        https://docs.cardano.org/en/latest/native-tokens/getting-started-with-native-tokens.html#start-the-minting-process
        :param asset_name: name component of the unique asset ID (<policy_id>.<asset_name>)
        :param payment_wallet: Wallet with sufficient funds to mint the token
        """
        #  Create a directory to hold intermediate files used to create the transaction
        tx_file_directory = create_intermediate_directory('tx')

        # ALWAYS work with a fresh set of protocol parameters.
        protocol_parameters = self.refresh_protocol_parameters()

        lovelace_unit = settings.LOVELACE_UNIT
        min_utxo_value = protocol_parameters['minUTxOValue']
        payment_address = from_wallet.payment_address
        token_lovelace = min_utxo_value * 3

        utxos = from_wallet.utxos
        lovelace_utxos = sort_utxos(filter_utxos(utxos, type=lovelace_unit), order='desc')
        if not lovelace_utxos:
            # Let there be be at least one UTxO containing purely ADA.
            # This will be used to pay for the transaction.
            raise CardanoError(f'Address {payment_address} has inadequate funds to complete transaction')

        # 1. Create a minting policy
        policy_signing_key_path = os.path.join(tx_file_directory, 'policy.skey')
        policy_verification_key_path = os.path.join(tx_file_directory, 'policy.vkey')
        policy_script_path = os.path.join(tx_file_directory, 'policy.script')
        self.cli.run('address key-gen', **{
            'signing-key-file': policy_signing_key_path,
            'verification-key-file': policy_verification_key_path,
        })
        policy_key_hash = self.cli.run('address key-hash', **{
            'payment-verification-key-file': policy_verification_key_path,
        })
        policy_info = {
            'keyHash': policy_key_hash,
            'type': 'sig',
        }
        with open(policy_script_path, 'w') as policy_script_file:
            json.dump(policy_info, policy_script_file)
        policy_id = self.cli.run('transaction policyid', **{
            'script-file': policy_script_path
        })

        # 2. Mint EXACTLY ONE token the new asset
        mint_argument = f'"1 {policy_id}.{asset_name}"'

        # ASSUMPTION: The payment wallet's largest ADA UTxO shall contain
        # sufficient ADA to pay for the transaction (including fees)
        lovelace_utxo = lovelace_utxos[0]
        total_lovelace_being_sent = lovelace_utxo['Tokens'][lovelace_unit]
        lovelace_to_return = total_lovelace_being_sent - token_lovelace

        tx_args = [
            ('tx-in', '{}#{}'.format(lovelace_utxo['TxHash'], lovelace_utxo['TxIx'])),
            ('tx-out', f'{payment_address}+{token_lovelace}+{mint_argument}'),
            ('tx-out', f'{payment_address}+{lovelace_to_return}')
        ]

        # 3. Build a draft transaction which will be used to calculate minimum fees
        # https://docs.cardano.org/en/latest/native-tokens/getting-started-with-native-tokens.html#build-the-raw-transaction
        draft_transaction_path = os.path.join(tx_file_directory, 'transaction.draft')
        self.cli.run('transaction build-raw', *tx_args, **{
            'fee': 0,
            'mint': mint_argument,
            'out-file': draft_transaction_path,
        })

        # 4. Calculate the minimum fee
        # https://docs.cardano.org/en/latest/native-tokens/getting-started-with-native-tokens.html#calculate-the-minimum-fee
        tx_fee = self.calculate_min_fee(**{
            'tx-body-file': draft_transaction_path,
            'tx-in-count': 1,
            'tx-out-count': 2,
            'witness-count': 2,
            'network': settings.NETWORK,
        })

        # 5. Update the "change" output to deduct the transaction fee
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-change-to-send-back-to-payment-addr
        tx_args[len(tx_args) - 1] = ('tx-out', f'{payment_address}+{lovelace_to_return - tx_fee}')

        self._submit_transaction(tx_file_directory, from_wallet, *tx_args, **{
            'fee': tx_fee,
            'mint': mint_argument,
        })

    def calculate_min_fee(self, **kwargs):
        kwargs.update({
            'protocol-params-file': self.protocol_parameters_path,
            'network': settings.NETWORK,
        })
        raw_response = self.cli.run('transaction calculate-min-fee', **kwargs)
        match = MIN_FEE_RE.match(raw_response)
        return int(match[1])

    # --------------------------------------------------------------------------
    # Internal methods
    # --------------------------------------------------------------------------
    def _submit_transaction(self, tx_file_directory, wallet, *tx_args, **tx_kwargs):
        raw_transaction_file = os.path.join(tx_file_directory, 'transaction.raw')

        # Determine the TTL (time to Live) for the transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#determine-the-ttl-time-to-live-for-the-transaction
        tip = self.query_tip()
        current_slot = int(tip['slot'])
        invalid_hereafter = current_slot + settings.DEFAULT_TRANSACTION_TTL

        tx_kwargs.update({
            'invalid-hereafter': invalid_hereafter,
            'out-file': raw_transaction_file,
        })
        self.cli.run('transaction build-raw', *tx_args, **tx_kwargs)

        # Sign the transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#sign-the-transaction
        signed_transaction_path = os.path.join(tx_file_directory, 'transaction.signed')

        signing_args = []
        signing_kwargs = {
            'tx-body-file': raw_transaction_file,
            'out-file': signed_transaction_path,
            'network': settings.NETWORK
        }

        signing_key_file_path = os.path.join(tx_file_directory, 'transaction.skey')
        with open(signing_key_file_path, 'w') as signing_key_file:
            json.dump(wallet.payment_signing_key, signing_key_file)
        signing_args.append(('signing-key-file', signing_key_file_path))

        if 'mint' in tx_kwargs:
            policy_signing_key_path = os.path.join(tx_file_directory, 'policy.skey')
            signing_args.append(('signing-key-file', policy_signing_key_path))

            policy_script_path = os.path.join(tx_file_directory, 'policy.script')
            signing_kwargs['script-file'] = policy_script_path

        self.cli.run('transaction sign', *signing_args, **signing_kwargs)

        # Submit the transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#submit-the-transaction
        self.cli.run('transaction submit', **{
            'tx-file': signed_transaction_path,
            'network': settings.NETWORK
        })

        # Clean up intermediate files
        shutil.rmtree(tx_file_directory)
