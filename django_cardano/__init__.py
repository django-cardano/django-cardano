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
    def __init__(self, source_error=None, stderr=None):
        self.reason = 'Cardano CLI command failed'
        self.return_code = -1

        if isinstance(source_error, subprocess.CalledProcessError):
            self.cmd = ' '.join(source_error.cmd)
            self.process_error = source_error
            self.return_code = source_error.returncode
            self.reason = str(source_error.stderr)
        elif isinstance(source_error, FileNotFoundError):
            self.reason = str(source_error)
        elif stderr:
            self.reason = str(stderr)

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

    def create_wallet(self, name):
        from django_cardano.models import Wallet
        wallet = Wallet(name=name)

        # Generate the payment signing & verification keys
        signing_key_path = os.path.join(settings.INTERMEDIATE_FILE_PATH, f'{wallet.id}.signing.key')
        verification_key_path = os.path.join(settings.INTERMEDIATE_FILE_PATH, f'{wallet.id}.verification.key')

        self.call_cli('address key-gen', **{
            'signing-key-file': signing_key_path,
            'verification-key-file': verification_key_path,
        })

        # Generate the stake signing & verification keys
        stake_signing_key_path = os.path.join(settings.INTERMEDIATE_FILE_PATH, f'{wallet.id}.stake_signing.key')
        stake_verification_key_path = os.path.join(settings.INTERMEDIATE_FILE_PATH, f'{wallet.id}.stake_verification.key')

        self.call_cli('stake-address key-gen', **{
            'signing-key-file': stake_signing_key_path,
            'verification-key-file': stake_verification_key_path,
        })

        # Create the payment address.
        wallet.payment_address = self.call_cli('address build', **{
            'payment-verification-key-file': verification_key_path,
            'stake-verification-key-file': stake_verification_key_path,
            'network': settings.NETWORK,
        })

        # Create the staking address.
        wallet.stake_address = self.call_cli('stake-address build', **{
            'stake-verification-key-file': stake_verification_key_path,
            'network': settings.NETWORK,
        })

        # Attach the generated key files to the wallet
        # Note: their stored values will be encrypted
        with open(signing_key_path, 'r') as signing_key_file:
            wallet.payment_signing_key = json.load(signing_key_file)
            os.remove(signing_key_path)
        with open(verification_key_path, 'r') as verification_key_file:
            wallet.payment_verification_key = json.load(verification_key_file)
            os.remove(verification_key_path)

        with open(stake_signing_key_path, 'r') as stake_signing_key_file:
            wallet.stake_signing_key = json.load(stake_signing_key_file)
            os.remove(stake_signing_key_path)
        with open(stake_verification_key_path, 'r') as stake_verification_key_file:
            wallet.stake_verification_key = json.load(stake_verification_key_file)
            os.remove(stake_verification_key_path)

        wallet.save()

        return wallet

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
            raise CardanoError(stderr=error_message)

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

        # Clean up intermediate files
        shutil.rmtree(tx_file_directory)

        return True


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
        network = options.get('network')
        if network:
            if network == 'mainnet':
                process_args.append('--mainnet')
            elif network == 'testnet':
                process_args.append('--testnet-magic')
                process_args.append(settings.TESTNET_MAGIC)
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

        try:
            completed_process = subprocess.run(
                process_args,
                check=True,
                capture_output=True,
                env={'CARDANO_NODE_SOCKET_PATH': settings.NODE_SOCKET_PATH}
            )
            if completed_process.returncode == 0:
                return completed_process.stdout.decode().strip()
            else:
                stderr = completed_process.stderr.decode().strip()
                raise CardanoError(stderr=stderr)

        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            raise CardanoError(source_error=e)

    def calculate_balance(self, utxos):
        return sum([int(utxo['Amount']) for utxo in utxos])

    def calculate_min_fee(self, **kwargs):
        kwargs['network'] = settings.NETWORK
        raw_response = self.call_cli('transaction calculate-min-fee', **kwargs)
        match = MIN_FEE_RE.match(raw_response)
        return int(match[1])
