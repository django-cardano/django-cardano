import subprocess


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
