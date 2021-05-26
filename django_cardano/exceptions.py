import subprocess
from enum import Enum

class CardanoErrorType(Enum):
    SIGNING_KEY_DECRYPTION_FAILURE = -2
    POLICY_SIGNING_KEY_DECRYPTION_FAILURE = -4


class CardanoError(Exception):
    def __init__(self, reason=None, source_error=None, code=-1):
        self.code = code
        self.reason = reason

        if source_error and not reason:
            self.reason = str(source_error)

        if isinstance(source_error, subprocess.CalledProcessError):
            cmd = source_error.cmd
            if isinstance(cmd, list):
                cmd = ' '.join(source_error.cmd)
            self.cmd = cmd
            self.process_error = source_error
            self.code = source_error.returncode
            self.reason = str(source_error.stderr)


    def __str__(self) -> str:
        return self.reason
