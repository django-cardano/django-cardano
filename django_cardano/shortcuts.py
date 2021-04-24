import os
from datetime import datetime, timezone

from django_cardano.settings import django_cardano_settings as settings


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def sort_utxos(utxos, type=settings.LOVELACE_UNIT, order='asc') -> list:
    if order == 'asc':
        return sorted(utxos, key=lambda v: v['Tokens'][type])
    else:
        return sorted(utxos, key=lambda v: v['Tokens'][type], reverse=True)


def create_intermediate_directory(*subpath_components) -> str:
    path_args = list(subpath_components)
    path_args.append(str(utc_now().timestamp()))

    path = os.path.join(settings.INTERMEDIATE_FILE_PATH, *path_args)
    os.makedirs(path, 0o755)
    return path
