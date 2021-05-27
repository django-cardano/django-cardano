import os
import re
from pathlib import Path

from django_cardano.settings import django_cardano_settings as settings

ALPHANUMERIC_RE = re.compile(r'[^a-zA-Z0-9]')


def filter_utxos(utxos, include=None, exclude=None) -> list:
    filtered_utxos = []
    lovelace_unit = settings.LOVELACE_UNIT

    for utxo in utxos:
        tokens = utxo['Tokens']
        asset_types = tuple(tokens.keys())

        if include == lovelace_unit:
            if len(asset_types) == 1:
                # Implicitly, if there is only one asset type in this UTxO
                # it MUST be lovelace. Cardano does not (yet) support the
                # notion of a UTxO without any amount of lovelace.
                filtered_utxos.append(utxo)
        elif include in asset_types:
            filtered_utxos.append(utxo)

        if exclude == lovelace_unit:
            if len(asset_types) > 1:
                # See logical explanation above.
                filtered_utxos.append(utxo)
            elif exclude not in asset_types:
                filtered_utxos.append(utxo)

    return filtered_utxos


def sort_utxos(utxos, type=settings.LOVELACE_UNIT, order='desc') -> list:
    if order == 'desc':
        return sorted(utxos, key=lambda v: v['Tokens'][type], reverse=True)
    else:
        return sorted(utxos, key=lambda v: v['Tokens'][type])


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
