from datetime import datetime, timezone

from django_cardano.settings import django_cardano_settings as settings

def utcnow() -> datetime:
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


def sort_utxos(utxos, type, order='asc') -> list:
    if order == 'asc':
        return sorted(utxos, key=lambda v: v['Tokens'][type])
    else:
        return sorted(utxos, key=lambda v: v['Tokens'][type], reverse=True)
