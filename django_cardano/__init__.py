import json
import os
import re
import shutil
import subprocess

from django_cardano.settings import django_cardano_settings as settings
from django_cardano.shortcuts import utcnow

# Result of 'transaction calculate-min-fee' command is expected to be of the
# exact form: '<int> Lovelace'
MIN_FEE_RE = re.compile(r'(\d+) Lovelace')


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

    def query_utxos(self, address, order=None) -> list:
        response = self.call_cli('query utxo', address=address, network=settings.NETWORK)
        lines = response.split('\n')
        headers = lines[0].split()

        utxos = []
        for line in lines[2:]:
            line_parts = line.split()
            utxos.append({
                headers[0]: line_parts[0],
                headers[1]: line_parts[1],
                headers[2]: int(line_parts[2]),
                'Unit': line_parts[3]
            })

        if order == 'asc':
            utxos.sort(key=lambda k: k["Amount"])
        elif order == 'desc':
            utxos.sort(key=lambda k: k["Amount"], reverse=True)

        return utxos

    def query_balance(self, address) -> int:
        utxos = self.query_utxos(address)

        if len(utxos) == 0:
            return 0

        return self.calculate_balance(utxos)

    def address_info(self, address):
        response = self.call_cli('address info', address=address)
        return json.loads(response)

    def send_payment(self, amount_to_send, from_wallet, to_address):
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
        # In an effort to keep the wallet transactions tidy, the idea here is to
        # exhaust all of the smallest UTxOs before moving on to the bigger ones.
        # Think of it like money: normally you'd reach for your change and small
        # bills before breaking out the $50 or $100 bills, right??
        utxos = self.query_utxos(from_address, order='asc')
        available_balance = self.calculate_balance(utxos)
        if amount_to_send > available_balance:
            error_message = f'Unable to transfer {amount_to_send} from {from_address} to {to_address}. Available funds: {available_balance}'
            raise CardanoError(reason=error_message)

        amount_being_sent = 0
        tx_in_list = []
        for utxo in utxos:
            tx_hash = utxo['TxHash']
            tx_index = utxo['TxIx']
            tx_in_list.append(('tx-in', f'{tx_hash}#{tx_index}'))
            amount_being_sent += utxo['Amount']

            if amount_being_sent >= amount_to_send:
                break

        # 3.3. Draft the transaction:
        # Produce a draft transaction in order to determine the fees required to perform the actual transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#draft-the-transaction

        # There will ALWAYS be exactly two output transactions:
        #   - The funds being sent to the recipient
        #   - The "change" being returned to the sender

        draft_tx_args = tx_in_list + [
            ('tx-out', f'{from_address}+0'),
            ('tx-out', f'{to_address}+0'),
        ]
        draft_transaction_path = os.path.join(tx_file_directory, 'transaction.draft')
        self.call_cli('transaction build-raw', *draft_tx_args, **{
            'invalid-hereafter': 0,
            'fee': 0,
            'out-file': draft_transaction_path
        })

        # 3.4. Calculate the fee
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-fee
        tx_fee = self.calculate_min_fee(**{
            'tx-body-file': draft_transaction_path,
            'protocol-params-file': self.protocol_parameters_path,
            'tx-in-count': len(tx_in_list),
            'tx-out-count': 2,
            'witness-count': 1,
            'byron-witness-count': 0,
        })

        # 3.5. Calculate the change to send back to payment.addr
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#calculate-the-change-to-send-back-to-payment-addr
        amount_to_return = amount_being_sent - amount_to_send - tx_fee

        # 3.6 Determine the TTL (time to Live) for the transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#determine-the-ttl-time-to-live-for-the-transaction
        tip = self.query_tip()
        current_slot = int(tip['slot'])
        invalid_hereafter = current_slot + settings.DEFAULT_TRANSACTION_TTL

        # 3.7. Build the transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#build-the-transaction
        actual_tx_args = tx_in_list + [
            ('tx-out', f'{to_address}+{amount_to_send}'),
            ('tx-out', f'{from_address}+{amount_to_return}'),
        ]
        raw_transaction_file = os.path.join(tx_file_directory, 'transaction.raw')

        self.call_cli('transaction build-raw', *actual_tx_args, **{
            'invalid-hereafter': invalid_hereafter,
            'fee': tx_fee,
            'out-file': raw_transaction_file
        })

        # 3.8. Sign the transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#sign-the-transaction
        signed_transaction_path = os.path.join(tx_file_directory, 'transaction.signed')
        signing_key_file_path = os.path.join(tx_file_directory, 'transaction.skey')
        with open(signing_key_file_path, 'w') as signing_key_file:
            json.dump(from_wallet.payment_signing_key, signing_key_file)

        self.call_cli('transaction sign', **{
            'tx-body-file': raw_transaction_file,
            'signing-key-file': signing_key_file_path,
            'out-file': signed_transaction_path,
            'network': settings.NETWORK
        })

        # 3.9. Submit the transaction
        # https://docs.cardano.org/projects/cardano-node/en/latest/stake-pool-operations/simple_transaction.html#submit-the-transaction
        self.call_cli('transaction submit', **{
            'tx-file': signed_transaction_path,
            'network': settings.NETWORK
        })

        # 8. Clean up intermediate files
        shutil.rmtree(tx_file_directory)

    def mint_native_tokens(self, quantity, asset_name, payment_wallet):
        """
        https://docs.cardano.org/en/latest/native-tokens/getting-started-with-native-tokens.html#start-the-minting-process
        :param payment_wallet: Wallet with sufficient funds to mint the token
        :return:
        """
        payment_address = payment_wallet.payment_address
        utxos = self.query_utxos(address=payment_address, order='desc')
        if len(utxos) < 1:
            raise CardanoError(f'Address {payment_address} has no available funds')
        payment_utxo = utxos[0]

        # 1a. Create a directory to hold intermediate files used to create the transaction
        tx_file_directory = os.path.join(settings.INTERMEDIATE_FILE_PATH, 'token', str(utcnow().timestamp()))
        os.makedirs(tx_file_directory, 0o755)

        # 1b. Create a minting policy
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
        mint_argument = f'"{quantity} {policy_id}.{asset_name}"'

        # 3. Build a draft transaction which will be used to calculate minimum fees
        # https://docs.cardano.org/en/latest/native-tokens/getting-started-with-native-tokens.html#build-the-raw-transaction
        draft_transaction_path = os.path.join(tx_file_directory, 'transaction.draft')
        tx_in = '{}#{}'.format(payment_utxo['TxHash'], payment_utxo['TxIx'])
        tx_out = f'{payment_address}+0+{mint_argument}'
        self.call_cli('transaction build-raw', **{
            'mary-era': None,
            'tx-in': tx_in,
            'tx-out': tx_out,
            'fee': 0,
            'mint': mint_argument,
            'out-file': draft_transaction_path,
            'shell': True,
        })

        # 4. Calculate the minimum fee
        # https://docs.cardano.org/en/latest/native-tokens/getting-started-with-native-tokens.html#calculate-the-minimum-fee
        tx_fee = self.calculate_min_fee(**{
            'tx-body-file': draft_transaction_path,
            'tx-in-count': 1,
            'tx-out-count': 1,
            'witness-count': 2,
            'protocol-params-file': self.protocol_parameters_path,
            'network': settings.NETWORK,
        })

        # 5. Build the transaction again, this time including the fee
        # https://docs.cardano.org/en/latest/native-tokens/getting-started-with-native-tokens.html#build-the-transaction-again
        # Note that the transaction fee is deducted from the amount ADA being returned
        raw_transaction_path = os.path.join(tx_file_directory, 'transaction.raw')
        amount_to_return = payment_utxo['Amount'] - tx_fee
        tx_out = f'{payment_address}+{amount_to_return}+{mint_argument}'
        self.call_cli('transaction build-raw', **{
            'mary-era': None,
            'tx-in': tx_in,
            'tx-out': tx_out,
            'fee': tx_fee,
            'mint': mint_argument,
            'out-file': raw_transaction_path,
            'shell': True,
        })

        # 6. Sign the transaction
        # https://docs.cardano.org/en/latest/native-tokens/getting-started-with-native-tokens.html#sign-the-transaction
        signed_transaction_path = os.path.join(tx_file_directory, 'transaction.signed')

        signing_key_file_path = os.path.join(tx_file_directory, 'transaction.skey')
        with open(signing_key_file_path, 'w') as signing_key_file:
            json.dump(payment_wallet.payment_signing_key, signing_key_file)

        signing_args = [
            ('signing-key-file', signing_key_file_path),
            ('signing-key-file', policy_signing_key_path),
        ]

        self.call_cli('transaction sign', *signing_args, **{
            'tx-body-file': raw_transaction_path,
            'script-file': policy_script_path,
            'out-file': signed_transaction_path,
            'network': settings.NETWORK
        })

        # 7. Submit the transaction
        # https://docs.cardano.org/en/latest/native-tokens/getting-started-with-native-tokens.html#submit-the-transaction
        self.call_cli('transaction submit', **{
            'tx-file': signed_transaction_path,
            'network': settings.NETWORK
        })

        # 8. Clean up intermediate files
        shutil.rmtree(tx_file_directory)

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

        shell = False
        if 'shell' in options:
            shell = options['shell']
            del options['shell']

        for option_name, option_value in options.items():
            process_args.append(f'--{option_name}')
            if option_value is not None:
                if isinstance(option_value, list):
                    process_args += option_value
                elif isinstance(option_value, tuple):
                    process_args += list(option_value)
                else:
                    process_args.append(str(option_value))

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
                    stderr = completed_process.stderr.decode().strip()
                    raise CardanoError(stderr=stderr)

            except (FileNotFoundError, subprocess.CalledProcessError) as e:
                raise CardanoError(source_error=e)

    @staticmethod
    def calculate_balance(utxos):
        return sum([int(utxo['Amount']) for utxo in utxos])

    def calculate_min_fee(self, **kwargs):
        kwargs['network'] = settings.NETWORK
        raw_response = self.call_cli('transaction calculate-min-fee', **kwargs)
        match = MIN_FEE_RE.match(raw_response)
        return int(match[1])
