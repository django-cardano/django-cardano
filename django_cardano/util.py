import json
import math
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .cli import CardanoCLI
from .settings import django_cardano_settings as settings

TOKEN_BUNDLE_RE = re.compile(r'(?:\".*?\"|\S)+')


def quot(a: int, b: int) -> int:
    return math.floor(a / b)


def roundup_bytes_to_words(b: int) -> int:
    return quot(b + 7, 8)


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

    @classmethod
    def token_bundle_info(cls, token_bundle: str) -> dict:
        """
        :param token_bundle: A token bundle
        :return: A dictionary containing three sets:
         - asset_ids: List of asset IDs in the bundle
         - policy_ids: Distinct set of policy IDs
         - asset_names: Distinct set of asset names
        """
        asset_ids = []
        tokens = defaultdict(int)
        distinct_policy_ids = set()
        distinct_asset_names = set()

        for bundle_entry in TOKEN_BUNDLE_RE.findall(token_bundle):
            bundle_entry = bundle_entry.strip('"')
            token_count, asset_id = bundle_entry.split(' ')
            tokens[asset_id] += int(token_count)
            asset_ids.append(asset_id)

            try:
                policy_id, asset_name = asset_id.split('.')
                distinct_policy_ids.add(policy_id)
                distinct_asset_names.add(asset_name)
            except ValueError:
                distinct_policy_ids.add(asset_id)

        return {
            'asset_ids': asset_ids,
            'distinct_policy_ids': distinct_policy_ids,
            'distinct_asset_names': distinct_asset_names,
            'tokens': tokens,
        }

    @classmethod
    def token_bundle_size(cls, token_bundle: str) -> int:
        """
        :param token_bundle: Full-string representation of token bundle
        :return: Size of the token bundle B in 8-byte long words

        num_assets: the number of distinct AssetIDs in token_bundle
        num_policy_ids: the number of distinct PolicyIDs in token_bundle
        sum_assetname_lengths: the sum of the length of the ByteStrings representing distinct asset names
        pid_size: the length of the hash of a policy (ie. the length of the PolicyID).
        bundle_size is the size of the token bundle B in 8-byte long words:
            ex: bundle_size = 6 + roundupBytesToWords(((numAssets B) * 12) +
                (sumAssetNameLengths B) + ((numPids B) * pid_size))

        TODO: Find an explanation for the '6' and '12' numbers in the above formula
        See: https://cardano-ledger.readthedocs.io/en/latest/explanations/min-utxo.html
        """
        bundle_info = cls.token_bundle_info(token_bundle)

        asset_ids = bundle_info['asset_ids']
        asset_names = bundle_info['distinct_asset_names']
        policy_ids = bundle_info['distinct_policy_ids']
        asset_name_lengths = sum([len(asset_name) for asset_name in asset_names])

        byte_count = (len(asset_ids) * 12) + asset_name_lengths
        for policy_id in policy_ids:
            # Note that policy IDs are represented as hexadecimal byte strings,
            # and one byte will contain two hex values, hence the division by 2.
            # Ex: 0xAF = 10101111 = one byte
            byte_count += math.ceil(len(policy_id) / 2)

        bundle_size = 6 + roundup_bytes_to_words(byte_count)
        return bundle_size

    @classmethod
    def min_token_dust_value(cls, token_bundle: str) -> int:
        """
        See: https://cardano-ledger.readthedocs.io/en/latest/explanations/min-utxo.html

        num_assets: the number of distinct AssetIDs in token_bundle
        num_policy_ids: the number of distinct PolicyIDs in token_bundle
        sum_assetname_lengths: the sum of the length of the ByteStrings representing distinct asset names
        pid_size: the length of the hash of a policy (ie. the length of the PolicyID).
        bundle_size is the size of the token bundle B in 8-byte long words:
            ex: bundle_size = 6 + roundupBytesToWords(((numAssets B) * 12) +
                (sumAssetNameLengths B) + ((numPids B) * pid_size))

        :return: Amount of lovelace (a.k.a. "dust") to accompany a UTxO containing non-ADA tokens
        """
        protocol_parameters = cls.refresh_protocol_parameters()
        min_utxo_value = protocol_parameters['minUTxOValue']
        utxo_entry_size_without_val = settings.UTXO_ENTRY_SIZE_WITHOUT_VAL
        ada_only_utxo_size = utxo_entry_size_without_val + settings.COIN_SIZE

        bundle_size = cls.token_bundle_size(token_bundle)
        return max(
            min_utxo_value,
            quot(min_utxo_value, ada_only_utxo_size) * (utxo_entry_size_without_val + bundle_size)
        )
