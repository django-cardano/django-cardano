import re
import subprocess

from django_cardano.settings import django_cardano_settings as settings

from .exceptions import CardanoError

# Output of 'transaction calculate-min-fee' command is presumed
# to be of the exact form: '<int> Lovelace'
MIN_FEE_RE = re.compile(r'(\d+)\s+Lovelace')

# Output of 'query utxo' command is presumed to yield an ASCII table
# containing rows of the form: <TxHash>    <TxIx>      <Amount>
UTXO_RE = re.compile(r'(\w+)\s+(\d+)\s+(.*)')


class CardanoCLI:
    @classmethod
    def run(cls, command, *args, **kwargs) -> str:
        """
        Invoke the specified cardano-cli command/subcommand
        The *args serve as a series of (arg_name, arg_value) tuples
        The **kwargs behave as singular command arguments.

        :param command: command/subcommand to invoke
        :param args:  Tuples containing optional argument name/value pairs
        :param kwargs: Additional argument name/value pairs
        :return: The cardano-cli command output written to stdout
        """
        process_args = [settings.CLI_PATH] + command.split()

        for arg in args:
            if isinstance(arg, str):
                process_args.append(f'--{arg}')
            elif isinstance(arg, tuple) and len(arg) == 2:
                process_args.append(f'--{arg[0]}')
                process_args.append(arg[1])

        options = dict(kwargs)
        if 'network' in options:
            if options['network'] == 'mainnet':
                process_args.append('--mainnet')
            elif options['network'] == 'testnet':
                process_args += ['--testnet-magic', str(settings.TESTNET_MAGIC)]
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

        subprocess_args = {
            'check': True,
            'capture_output': True,
            'env': {'CARDANO_NODE_SOCKET_PATH': settings.NODE_SOCKET_PATH},
        }

        if command == 'transaction build-raw':
            # Iff the CLI command being invoked is "transaction build-raw",
            # issue the stringified command in shell mode.
            #
            # The reason for this is that  when performing a transaction
            # that involved native tokens, the --tx-out argument(s) contain
            # a space (ex: <addr>+<lovelace>+"<quantity> <asset_id>").
            #
            # For whatever reason, subprocess.run(...) does not permit the use
            # of arguments containing spaces, thus the composed/executed command
            # is not of the intended form and will consequently fail.
            #
            # Enabling shell mode allows the command to be invoked with spaces
            # and quotation characters intact.
            subprocess_args['shell'] = True
            process_args = ' '.join(process_args)

        try:
            completed_process = subprocess.run(process_args, **subprocess_args)
            if completed_process.returncode == 0:
                return completed_process.stdout.decode().strip()
            else:
                error_message = completed_process.stderr.decode().strip()
                raise CardanoError(error_message)
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            raise CardanoError(source_error=e)
