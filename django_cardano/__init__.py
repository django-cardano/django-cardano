import json
import subprocess

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
    def query_tip(self) -> str:
        return json.loads(self._call_cli('query', 'tip'))


    # --------------------------------------------------------------------------
    def _call_cli(self, *args, **kwargs):
        """
        Invoke the cardano-cli command/subcommand specified by the given *args
        The **kwargs shall behave as options to the command.
        Options
        :param args: command/subcommand to invoke
        :param kwargs: Arguments supplied to the
        :return:
        """
        process_args = [settings.CARDANO_CLI_PATH] + list(args)

        for option_name, option_value in kwargs.items():
            process_args.append(f'--{option_name}')
            if option_value:
                if isinstance(option_value, str):
                    process_args.append(option_value)
                else:
                    process_args += list(option_value)

        if settings.NETWORK == 'mainnet':
            process_args.append('--mainnet')
        else:
            process_args += ['--testnet-magic', settings.TESTNET_MAGIC]

        try:
            completed_process = subprocess.run(
                process_args,
                check=True,
                capture_output=True,
                env={'CARDANO_NODE_SOCKET_PATH': settings.CARDANO_NODE_SOCKET_PATH}
            )
            if completed_process.returncode == 0:
                return completed_process.stdout.decode().strip()
            else:
                stderr = completed_process.stderr.decode().strip()
                raise CardanoError(stderr=stderr)

        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            raise CardanoError(source_error=e)
