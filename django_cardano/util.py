import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .cli import CardanoCLI
from .settings import django_cardano_settings as settings


class CardanoUtils:
    protocol_parameters_path = Path(settings.APP_DATA_PATH, 'protocol.json')

    @classmethod
    def refresh_protocol_parameters(cls, force=False) -> dict:
        if not os.path.exists(settings.APP_DATA_PATH):
            os.makedirs(settings.APP_DATA_PATH, 0o755)

        load = True
        if cls.protocol_parameters_path.exists():
            if force or not settings.PROTOCOL_TTL:
                load = True
            else:
                file_stats = cls.protocol_parameters_path.stat()
                date_modified = datetime.fromtimestamp(file_stats.st_mtime, tz=timezone.utc)
                now = datetime.now(tz=timezone.utc)
                file_age = now - date_modified
                load = True if file_age.seconds > settings.PROTOCOL_TTL else False

        if load:
            CardanoCLI.run('query protocol-parameters', **{
                'network': settings.NETWORK,
                'out-file': cls.protocol_parameters_path,
            })

        with open(cls.protocol_parameters_path, 'r') as protocol_parameters_file:
            protocol_parameters = json.load(protocol_parameters_file)

        return protocol_parameters

    @classmethod
    def query_tip(cls) -> dict:
        response = CardanoCLI.run('query tip', network=settings.NETWORK)
        return json.loads(response)

    @classmethod
    def address_info(cls, address):
        response = CardanoCLI.run('address info', address=address)
        return json.loads(response)

    @classmethod
    def tx_info(cls, tx_file):
        return CardanoCLI.run('transaction view', **{
            'tx-file': tx_file
        })
