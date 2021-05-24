import json
import os
from pathlib import Path

from .cli import CardanoCLI
from .settings import django_cardano_settings as settings

class CardanoUtils:
    cli = CardanoCLI()
    protocol_parameters_path = Path(settings.APP_DATA_PATH, 'protocol.json')


    @classmethod
    def refresh_protocol_parameters(cls) -> dict:
        if not os.path.exists(settings.APP_DATA_PATH):
            os.makedirs(settings.APP_DATA_PATH, 0o755)

        cls.cli.run('query protocol-parameters', **{
            'network': settings.NETWORK,
            'out-file': cls.protocol_parameters_path,
        })

        with open(cls.protocol_parameters_path, 'r') as protocol_parameters_file:
            protocol_parameters = json.load(protocol_parameters_file)

        return protocol_parameters

    @classmethod
    def query_tip(cls) -> dict:
        response = cls.cli.run('query tip', network=settings.NETWORK)
        return json.loads(response)

    @classmethod
    def address_info(cls, address):
        response = cls.cli.run('address info', address=address)
        return json.loads(response)
