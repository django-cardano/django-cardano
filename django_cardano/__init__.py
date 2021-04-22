import json
import os
import subprocess

from pathlib import Path

from django_cardano.settings import django_cardano_settings as settings


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

    def query_tip(self) -> str:
        response = self.call_cli('query tip', network=settings.NETWORK)
        return json.loads(response)

    def query_utxos(self, address) -> list:
        response = self.call_cli('query utxo', address=address, network=settings.NETWORK)
        lines = response.split('\n')
        headers = lines[0].split()

        utxos = []
        for line in lines[2:]:
            line_parts = line.split()
            utxos.append({
                headers[0]: line_parts[0],
                headers[1]: line_parts[1],
                headers[2]: line_parts[2],
                'Unit': line_parts[3]
            })

        return utxos

    def query_balance(self, address) -> int:
        utxos = self.query_utxos(address)

        if len(utxos) == 0:
            return 0

        return sum([int(utxo['Amount']) for utxo in utxos])

    def create_wallet(self, name):
        from django_cardano.models import Wallet
        wallet = Wallet(name=name)

        # Generate the payment signing & verification keys
        signing_key_path = os.path.join(settings.INTERMEDIATE_FILE_PATH, f'{wallet.id}.signing.key')
        verification_key_path = os.path.join(settings.INTERMEDIATE_FILE_PATH, f'{wallet.id}.verification.key')
        payment_address_path = os.path.join(settings.INTERMEDIATE_FILE_PATH, f'{wallet.id}.payment.addr')

        self.call_cli('address key-gen', **{
            'signing-key-file': signing_key_path,
            'verification-key-file': verification_key_path,
        })

        # Generate the stake signing & verification keys
        stake_signing_key_path = os.path.join(settings.INTERMEDIATE_FILE_PATH, f'{wallet.id}.stake_signing.key')
        stake_verification_key_path = os.path.join(settings.INTERMEDIATE_FILE_PATH, f'{wallet.id}.stake_verification.key')
        stake_address_path = os.path.join(settings.INTERMEDIATE_FILE_PATH, f'{wallet.id}.stake.addr')

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

    def send_payment(self, from_address, to_address, amount):
        return {}

    # --------------------------------------------------------------------------
    def call_cli(self, command, *args, **kwargs):
        """
        Invoke the cardano-cli command/subcommand specified by the given *args
        The **kwargs shall behave as options to the command.
        Options
        :param args: command/subcommand to invoke
        :param kwargs: Arguments supplied to the
        :return:
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
            if option_value:
                if isinstance(option_value, str):
                    process_args.append(option_value)
                else:
                    process_args += list(option_value)

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
