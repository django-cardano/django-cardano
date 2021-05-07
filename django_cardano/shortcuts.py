import os
import re
from pathlib import Path

from django_cardano.settings import django_cardano_settings as settings

ALPHANUMERIC_RE = re.compile(r'[^a-zA-Z0-9]')


def filter_utxos(utxos, type) -> list:
    filtered_utxos = []
    lovelace_unit = settings.LOVELACE_UNIT

    for utxo in utxos:
        tokens = utxo['Tokens']
        asset_types = tuple(tokens.keys())

        if type == lovelace_unit:
            if len(asset_types) == 1 and asset_types[0] == lovelace_unit:
                filtered_utxos.append(utxo)
        elif type in asset_types:
            filtered_utxos.append(utxo)

    return filtered_utxos


def sort_utxos(utxos, type=settings.LOVELACE_UNIT, order='desc') -> list:
    if order == 'desc':
        return sorted(utxos, key=lambda v: v['Tokens'][type], reverse=True)
    else:
        return sorted(utxos, key=lambda v: v['Tokens'][type])


def create_intermediate_directory(*subpath_components) -> Path:
    path = Path(settings.INTERMEDIATE_FILE_PATH, *subpath_components)
    os.makedirs(path, 0o755)
    return path


def clean_token_asset_name(asset_name: str) -> str:
    """
    :param asset_name: The asset_name segment of a Cardano native token

    Cardano native assets are identified by the concatenation of
    their policy ID and an optional name:
    <asset_id> = <policy_id>.<asset_name>

    The asset name is restricted to alphanumeric characters, so
    use this shortcut to exclude invalid characters.
    """
    return ALPHANUMERIC_RE.sub('', asset_name)
