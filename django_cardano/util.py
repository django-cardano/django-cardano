import json
import os
from pathlib import Path

from .cli import CardanoCLI
from .settings import django_cardano_settings as settings


class CardanoUtils:
    def __init__(self) -> None:
        super().__init__()

        self.cli = CardanoCLI()
        
        if not os.path.exists(settings.INTERMEDIATE_FILE_PATH):
            os.makedirs(settings.INTERMEDIATE_FILE_PATH, 0o755)

        self.protocol_parameters_path = Path(settings.INTERMEDIATE_FILE_PATH, 'protocol.json')

    def refresh_protocol_parameters(self) -> dict:
        self.cli.run('query protocol-parameters', **{
            'network': settings.NETWORK,
            'out-file': self.protocol_parameters_path,
        })

        with open(self.protocol_parameters_path, 'r') as protocol_parameters_file:
            protocol_parameters = json.load(protocol_parameters_file)

        return protocol_parameters

    def query_tip(self) -> dict:
        response = self.cli.run('query tip', network=settings.NETWORK)
        return json.loads(response)

    def address_info(self, address):
        response = self.cli.run('address info', address=address)
        return json.loads(response)
