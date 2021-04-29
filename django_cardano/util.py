import json
import os

from .cli import CardanoCLI
from .settings import django_cardano_settings as settings


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

    def address_info(self, address):
        response = self.cli.run('address info', address=address)
        return json.loads(response)