from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc)

def filter_utxos(utxos, type):
    filtered_utxos = {}

    for tx_hash, tx_info in utxos.items():
        assets = tx_info['Assets']
        asset_types = tuple(assets.keys())

        if type == 'lovelace':
            if len(asset_types) == 1 and asset_types[0] == 'lovelace':
                filtered_utxos[tx_hash] = tx_info
        elif type in asset_types:
            filtered_utxos[tx_hash] = tx_info

    return filtered_utxos
