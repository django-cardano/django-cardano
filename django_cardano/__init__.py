import json
import os
import re
import shutil
import subprocess

from collections import defaultdict

from django_cardano.settings import django_cardano_settings as settings
from django_cardano.shortcuts import (
    filter_utxos,
    sort_utxos,
    utcnow,
)

# Output of 'transaction calculate-min-fee' command is presumed
# to be of the exact form: '<int> Lovelace'
MIN_FEE_RE = re.compile(r'(\d+)\s+Lovelace')

# Output of 'query utxo' command is presumed to yield an ASCII table
# containing rows of the form: <TxHash>    <TxIx>      <Amount>
UTXO_RE = re.compile(r'(\w+)\s+(\d+)\s+(.*)')


class CardanoError(RuntimeError):
    def __init__(self, reason=None, source_error=None):
        self.reason = reason if reason else 'Cardano CLI command failed'
        self.return_code = -1

        if isinstance(source_error, subprocess.CalledProcessError):
            cmd = source_error.cmd
            if isinstance(cmd, list):
                cmd = ' '.join(source_error.cmd)
            self.cmd = cmd
            self.process_error = source_error
            self.return_code = source_error.returncode
            self.reason = str(source_error.stderr)
        elif isinstance(source_error, FileNotFoundError):
            self.reason = str(source_error)

    def __str__(self) -> str:
        return self.reason


class Cardano:
    def __init__(self) -> None:
        super().__init__()

        if not os.path.exists(settings.INTERMEDIATE_FILE_PATH):
            os.makedirs(settings.INTERMEDIATE_FILE_PATH, 0o755)

        self.protocol_parameters_path = os.path.join(settings.INTERMEDIATE_FILE_PATH, 'protocol.json')

    def refresh_protocol_parameters(self) -> dict:
        protocol_params_raw = self.call_cli('query protocol-parameters', network=settings.NETWORK)
        protocol_parameters = json.loads(protocol_params_raw)
        with open(self.protocol_parameters_path, 'w') as protocol_parameters_file:
            json.dump(protocol_parameters, protocol_parameters_file)

        return protocol_parameters

    def query_tip(self) -> dict:
        response = self.call_cli('query tip', network=settings.NETWORK)
        return json.loads(response)

    def query_utxos(self, address) -> list:
        response = self.call_cli('query utxo', address=address, network=settings.NETWORK)
        lines = response.split('\n')

        utxos = []
        for line in lines[2:]:
            match = UTXO_RE.match(line)
            utxo_info = {
                'TxHash': match[1],
                'TxIx': match[2],
                'Tokens': defaultdict(int),
            }

            tokens = match[3].split('+')
            for token in tokens:
                token_info = token.split()
                asset_count = int(token_info[0])
                asset_type = token_info[1]
                utxo_info['Tokens'][asset_type] += asset_count
            utxos.append(utxo_info)

        return utxos

    def query_balance(self, address) -> tuple:
        utxos = self.query_utxos(address)

        all_tokens = defaultdict(int)
        for utxo in utxos:
            utxo_tokens = utxo['Tokens']
            for token_id, token_count in utxo_tokens.items():
                all_tokens[token_id] += token_count

        return all_tokens, utxos

    def address_info(self, address):
        response = self.call_cli('address info', address=address)
        return json.loads(response)

    def consolidate_tokens(self, wallet):
        tx_file_directory = os.path.join(settings.INTERMEDIATE_FILE_PATH, str(utcnow().timestamp()))
        os.makedirs(tx_file_directory, 0o755)

        protocol_parameters = self.refresh_protocol_parameters()
        min_utxo_value = protocol_parameters['minUTxOValue']

        # HACK!! How do we compute the actual amount of lovelace that
        # is required to be attached to a token??
        token_lovelace = min_utxo_value * 2

        payment_address = wallet.payment_address
        all_tokens, utxos = self.query_balance(payment_address)

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
        self.call_cli('transaction build-raw', *tx_args, **{
            'invalid-hereafter': 0,
            'fee': 0,
            'out-file': draft_transaction_path
        })

        tx_fee = self._calculate_min_fee(**{
            'tx-body-file': draft_transaction_path,
            'protocol-params-file': self.protocol_parameters_path,
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

        self._submit_transaction(tx_args, tx_fee, tx_file_directory, wallet)

    def send_lovelace(self, lovelace_requested, from_wallet, to_address):
        # Create a directory to hold intermediate files used to create the transaction
        tx_file_directory = os.path.join(settings.INTERMEDIATE_FILE_PATH, str(utcnow().timestamp()))
        os.makedirs(tx_file_directory, 0o755)

        from_address = from_wallet.payment_address

        # 3.1. Get protocol parameters
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#get-protocol-parameters
        # ALWAYS work with a fresh set of protocol parameters.
        self.refresh_protocol_parameters()

        # 3.2. Get the transaction hash and index of the UTXO to spend
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#get-the-transaction-hash-and-index-of-the-utxo-to-spend
        #
        # In an effort to keep the wallet UTxOs tidy, the idea here is to
        # exhaust all of the smallest UTxOs before moving on to the bigger ones.
        # Think of it like money: normally you'd spend your change and small
        # bills before breaking out the benjies.
        utxos = self.query_utxos(from_address)
        sorted_lovelace_utxos = sort_utxos(
            filter_utxos(utxos, type=settings.LOVELACE_UNIT),
            type=settings.LOVELACE_UNIT,
        )

        tx_in_list = []
        total_lovelace_being_sent = 0
        for utxo in sorted_lovelace_utxos:
            tx_hash = utxo['TxHash']
            tx_index = utxo['TxIx']
            tx_in_list.append(('tx-in', f'{tx_hash}#{tx_index}'))
            total_lovelace_being_sent += utxo['Tokens'][settings.LOVELACE_UNIT]
            if total_lovelace_being_sent >= lovelace_requested:
                break

        # 3.3. Draft the transaction:
        # Produce a draft transaction in order to determine the fees required to perform the actual transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#draft-the-transaction

        # There will ALWAYS be exactly two output transactions:
        #   - The funds being sent to the recipient
        #   - The "change" being returned to the sender
        tx_out_list = [
            ('tx-out', f'{to_address}+{lovelace_requested}'),
            ('tx-out', f'{from_address}+{total_lovelace_being_sent}'),
        ]

        tx_args = tx_in_list + tx_out_list
        draft_transaction_path = os.path.join(tx_file_directory, 'transaction.draft')
        self.call_cli('transaction build-raw', *tx_args, **{
            'fee': 0,
            'out-file': draft_transaction_path
        })

        # 3.4. Calculate the fee
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-fee
        tx_fee = self._calculate_min_fee(**{
            'tx-body-file': draft_transaction_path,
            'protocol-params-file': self.protocol_parameters_path,
            'tx-in-count': len(tx_in_list),
            'tx-out-count': len(tx_out_list),
            'witness-count': 1,
            'byron-witness-count': 0,
        })

        # 3.5. Calculate the change to send back to payment.addr and update that output respectively
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-change-to-send-back-to-payment-addr
        lovelace_to_return = total_lovelace_being_sent - lovelace_requested - tx_fee
        tx_args[len(tx_args) - 1] = ('tx-out', f'{from_address}+{lovelace_to_return}')

        self._submit_transaction(tx_args, tx_fee, tx_file_directory, from_wallet)

    def send_tokens(self, tokens_requested, token_id, from_wallet, to_address):
        # ALWAYS work with a fresh set of protocol parameters.
        protocol_parameters = self.refresh_protocol_parameters()

        lovelace_unit = settings.LOVELACE_UNIT
        min_utxo_value = protocol_parameters['minUTxOValue']
        payment_address = from_wallet.payment_address
        token_lovelace = min_utxo_value * 3

        # Create a directory to hold intermediate files used to create the transaction
        tx_file_directory = os.path.join(settings.INTERMEDIATE_FILE_PATH, str(utcnow().timestamp()))
        os.makedirs(tx_file_directory, 0o755)

        utxos = self.query_utxos(payment_address)
        lovelace_utxos = sort_utxos(filter_utxos(utxos, type=lovelace_unit), order='desc')
        token_utxos = sort_utxos(filter_utxos(utxos, type=token_id), type=token_id)

        if not lovelace_utxos:
            # Let there be be at least one UTxO containing purely ADA.
            # This will be used to pay for the transaction.
            raise CardanoError('Insufficient ADA funds to complete transaction')

        # ASSUMPTION: The largest ADA UTxO shall contain sufficient ADA
        # to pay for the transaction (including fees)
        lovelace_utxo = lovelace_utxos[0]
        total_lovelace_being_sent = lovelace_utxo['Tokens'][lovelace_unit]
        tx_in_list = [('tx-in', '{}#{}'.format(lovelace_utxo['TxHash'], lovelace_utxo['TxIx']))]

        # The set of transaction inputs shall be comprised of as many token UTxOs
        # as are required to accommodate the tokens_requested
        total_tokens_being_sent = 0
        for utxo in token_utxos:
            tx_hash = utxo['TxHash']
            tx_index = utxo['TxIx']
            tx_in_list.append(('tx-in', f'{tx_hash}#{tx_index}'))
            total_tokens_being_sent += utxo['Tokens'][token_id]

            # Accumulate the total amount of lovelace being sent
            total_lovelace_being_sent += utxo['Tokens'][lovelace_unit]

            if total_tokens_being_sent >= tokens_requested:
                break

        if total_tokens_being_sent < tokens_requested:
            raise CardanoError(f'Insufficient tokens. Requested: {tokens_requested}, Available: {total_tokens_being_sent}')

        lovelace_to_return = total_lovelace_being_sent

        # Let the first transaction output represent the tokens being sent to the recipient
        tx_out_list = [('tx-out', f'{to_address}+{token_lovelace}+"{tokens_requested} {token_id}"')]
        lovelace_to_return -= token_lovelace

        # If there are more tokens in this wallet than are being sent, return the rest to the sender
        tokens_to_return = total_tokens_being_sent - tokens_requested
        if tokens_to_return > 0:
            tx_out_list.append(('tx-out', f'{payment_address}+{token_lovelace}+"{tokens_to_return} {token_id}"'))
            lovelace_to_return -= token_lovelace

        # The last output represents the lovelace being returned to the payment wallet
        tx_out_list.append(('tx-out', f'{payment_address}+{lovelace_to_return}'))

        tx_args = tx_in_list + tx_out_list
        draft_transaction_path = os.path.join(tx_file_directory, 'transaction.draft')
        self.call_cli('transaction build-raw', *tx_args, **{
            'mary-era': None,
            'fee': 0,
            'out-file': draft_transaction_path
        })

        # 3.4. Calculate the fee
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-fee
        tx_fee = self._calculate_min_fee(**{
            'tx-body-file': draft_transaction_path,
            'protocol-params-file': self.protocol_parameters_path,
            'tx-in-count': len(tx_in_list),
            'tx-out-count': len(tx_out_list),
            'witness-count': 1,
            'byron-witness-count': 0,
        })

        # 3.5. Calculate the change to send back to payment.addr and update that output respectively
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-change-to-send-back-to-payment-addr
        tx_args[len(tx_args) - 1] = ('tx-out', f'{payment_address}+{lovelace_to_return - tx_fee}')

        self._submit_transaction(tx_args, tx_fee, tx_file_directory, from_wallet)

    def mint_nft(self, asset_name, payment_wallet):
        """
        https://docs.cardano.org/en/latest/native-tokens/getting-started-with-native-tokens.html#start-the-minting-process
        :param payment_wallet: Wallet with sufficient funds to mint the token
        :return:
        """
        #  Create a directory to hold intermediate files used to create the transaction
        tx_file_directory = os.path.join(settings.INTERMEDIATE_FILE_PATH, 'token', str(utcnow().timestamp()))
        os.makedirs(tx_file_directory, 0o755)

        # ALWAYS work with a fresh set of protocol parameters.
        protocol_parameters = self.refresh_protocol_parameters()

        lovelace_unit = settings.LOVELACE_UNIT
        min_utxo_value = protocol_parameters['minUTxOValue']
        payment_address = payment_wallet.payment_address
        token_lovelace = min_utxo_value * 3

        utxos = self.query_utxos(payment_address)
        lovelace_utxos = sort_utxos(filter_utxos(utxos, type=lovelace_unit), order='desc')
        if not lovelace_utxos:
            # Let there be be at least one UTxO containing purely ADA.
            # This will be used to pay for the transaction.
            raise CardanoError(f'Address {payment_address} has inadequate funds to complete transaction')

        # 1. Create a minting policy
        policy_signing_key_path = os.path.join(tx_file_directory, 'policy.skey')
        policy_verification_key_path = os.path.join(tx_file_directory, 'policy.vkey')
        policy_script_path = os.path.join(tx_file_directory, 'policy.script')
        self.call_cli('address key-gen', **{
            'signing-key-file': policy_signing_key_path,
            'verification-key-file': policy_verification_key_path,
        })
        policy_key_hash = self.call_cli('address key-hash', **{
            'payment-verification-key-file': policy_verification_key_path,

        })
        with open(policy_script_path, 'w') as policy_script_file:
            json.dump({
                'keyHash': policy_key_hash,
                'type': 'sig',
            }, policy_script_file)

        # 2. Mint the new asset
        policy_id = self.call_cli('transaction policyid', **{
            'script-file': policy_script_path
        })
        mint_argument = f'"1 {policy_id}.{asset_name}"'

        # ASSUMPTION: The largest ADA UTxO shall contain sufficient ADA
        # to pay for the transaction (including fees)
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
        self.call_cli('transaction build-raw', *tx_args, **{
            'mary-era': None,
            'fee': 0,
            'mint': mint_argument,
            'out-file': draft_transaction_path,
        })

        # 4. Calculate the minimum fee
        # https://docs.cardano.org/en/latest/native-tokens/getting-started-with-native-tokens.html#calculate-the-minimum-fee
        tx_fee = self._calculate_min_fee(**{
            'tx-body-file': draft_transaction_path,
            'tx-in-count': 1,
            'tx-out-count': 2,
            'witness-count': 2,
            'protocol-params-file': self.protocol_parameters_path,
            'network': settings.NETWORK,
        })

        # 5. Update the "change" output to deduct the transaction fee
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-change-to-send-back-to-payment-addr
        tx_args[len(tx_args) - 1] = ('tx-out', f'{payment_address}+{lovelace_to_return - tx_fee}')

        self._submit_transaction(tx_args, tx_fee, tx_file_directory, payment_wallet, mint=mint_argument)

    # --------------------------------------------------------------------------
    def call_cli(self, command, *args, **kwargs):
        """
        Invoke the specified cardano-cli command/subcommand
        The *args serve as a series of (arg_name, arg_value) tuples
        The **kwargs behave as singular command arguments.

        :param command: command/subcommand to invoke
        :param args: command/subcommand to invoke
        :param kwargs: Arguments supplied to the
        :return: The cardano-cli command output written to stdout
        """
        process_args = [settings.CLI_PATH] + command.split()

        for arg in args:
            if isinstance(arg, str):
                process_args.append(arg)
            elif isinstance(arg, tuple) and len(arg) == 2:
                process_args.append(f'--{arg[0]}')
                process_args.append(arg[1])

        options = dict(kwargs)
        if 'network' in options:
            if options['network'] == 'mainnet':
                process_args.append('--mainnet')
            elif options['network'] == 'testnet':
                process_args += ['--testnet-magic', settings.TESTNET_MAGIC]
            del options['network']

        for option_name, option_value in options.items():
            process_args.append(f'--{option_name}')
            if option_value is not None:
                if isinstance(option_value, list):
                    process_args += option_value
                elif isinstance(option_value, tuple):
                    process_args += list(option_value)
                else:
                    process_args.append(str(option_value))

        shell = True if command == 'transaction build-raw' else False
        if shell:
            try:
                command = ' '.join(process_args)
                completed_process = subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    env={'CARDANO_NODE_SOCKET_PATH': settings.NODE_SOCKET_PATH},
                    shell=True
                )
                if completed_process.returncode == 0:
                    return True
                else:
                    raise CardanoError(f'Subprocess command failed: {command}')
            except (FileNotFoundError, subprocess.CalledProcessError) as e:
                raise CardanoError(source_error=e)
        else:
            try:
                completed_process = subprocess.run(
                    process_args,
                    check=True,
                    capture_output=True,
                    env={'CARDANO_NODE_SOCKET_PATH': settings.NODE_SOCKET_PATH},
                )
                if completed_process.returncode == 0:
                    return completed_process.stdout.decode().strip()
                else:
                    error_message = completed_process.stderr.decode().strip()
                    raise CardanoError(error_message)

            except (FileNotFoundError, subprocess.CalledProcessError) as e:
                raise CardanoError(source_error=e)

    # --------------------------------------------------------------------------
    def _submit_transaction(self, tx_args, tx_fee, tx_file_directory, wallet, **kwargs):
        raw_transaction_file = os.path.join(tx_file_directory, 'transaction.raw')

        # Determine the TTL (time to Live) for the transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#determine-the-ttl-time-to-live-for-the-transaction
        tip = self.query_tip()
        current_slot = int(tip['slot'])
        invalid_hereafter = current_slot + settings.DEFAULT_TRANSACTION_TTL

        transaction_options = {
            'mary-era': None,
            'invalid-hereafter': invalid_hereafter,
            'fee': tx_fee,
            'out-file': raw_transaction_file,
            **kwargs,
        }
        self.call_cli('transaction build-raw', *tx_args, **transaction_options)

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

        if 'mint' in kwargs:
            policy_signing_key_path = os.path.join(tx_file_directory, 'policy.skey')
            signing_args.append(('signing-key-file', policy_signing_key_path))

            policy_script_path = os.path.join(tx_file_directory, 'policy.script')
            signing_kwargs['script-file'] = policy_script_path

        self.call_cli('transaction sign', *signing_args, **signing_kwargs)

        # Submit the transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#submit-the-transaction
        self.call_cli('transaction submit', **{
            'tx-file': signed_transaction_path,
            'network': settings.NETWORK
        })

        # Clean up intermediate files
        shutil.rmtree(tx_file_directory)

    def _calculate_min_fee(self, **kwargs):
        kwargs['network'] = settings.NETWORK
        raw_response = self.call_cli('transaction calculate-min-fee', **kwargs)
        match = MIN_FEE_RE.match(raw_response)
        return int(match[1])
