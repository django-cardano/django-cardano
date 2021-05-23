import os
import random
import shutil
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.utils.text import slugify

from .exceptions import CardanoError
from .models import (
    get_minting_policy_model,
    get_transaction_model,
    get_wallet_model,
)
from .settings import django_cardano_settings
from .util import CardanoUtils

MintingPolicy = get_minting_policy_model()
Transaction = get_transaction_model()
Wallet = get_wallet_model()

DEFAULT_SPENDING_PASSWORD = 'fL;$qR9FZ3?stf-M'
DEFAULT_MINTING_PASSWORD = 'eMgP3AjU&6KRVTrU'


def data_path_for_model(instance):
    base_path = Path(django_cardano_settings.APP_DATA_PATH)
    model_name = slugify(instance._meta.verbose_name)
    return base_path / model_name / str(instance.id)


class DjangoCardanoTestCase(TestCase):
    wallet = None

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        test_data_path = settings.PROJECT_PATH / 'data' / 'test'
        cls.wallet = Wallet.objects.create_from_path(test_data_path)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()

        # Discard the associated key files
        shutil.rmtree(data_path_for_model(cls.wallet))

    def test_query_tip(self):
        tip_info = CardanoUtils.query_tip()

        self.assertIn('block', tip_info)
        self.assertIn('epoch', tip_info)
        self.assertIn('hash', tip_info)
        self.assertIn('slot', tip_info)

    def test_create_wallet(self):
        try:
            wallet = Wallet.objects.create(name='Test Wallet')
            wallet.generate_keys(DEFAULT_SPENDING_PASSWORD)

            address_info = CardanoUtils.address_info(wallet.payment_address)
            self.assertEqual(address_info['type'], 'payment')
            self.assertEqual(address_info['encoding'], 'bech32')
            self.assertEqual(address_info['era'], 'shelley')
            self.assertEqual(address_info['address'], wallet.payment_address)

            shutil.rmtree(data_path_for_model(wallet))
        except CardanoError as e:
            print(e)

    def test_get_address_info(self):
        address_info = CardanoUtils.address_info(self.wallet.payment_address)
        self.assertTrue(isinstance(address_info, dict))

    def test_get_utxos(self):
        utxos = self.wallet.utxos
        self.assertTrue(isinstance(utxos, list))

        for utxo in utxos:
            self.assertIn('TxHash', utxo)
            self.assertIn('TxIx', utxo)
            self.assertIn('Tokens', utxo)

    def test_get_wallet_balance(self):
        tokens, _ = self.wallet.balance
        self.assertTrue(isinstance(tokens, dict))

    def test_send_lovelace(self):
        lovelace_requested = 1000000
        to_address = self.wallet.payment_address

        draft_transaction = self.wallet.send_lovelace(
            lovelace_requested,
            to_address=to_address,
        )
        draft_tx_fee = draft_transaction.calculate_min_fee()

        self.assertTrue(isinstance(draft_transaction, Transaction))
        self.assertTrue(isinstance(draft_tx_fee, int))
        self.assertTrue(draft_transaction._state.adding)
        self.assertTrue(draft_transaction.intermediate_file_path.exists)

        intermediate_file_path = draft_transaction.intermediate_file_path
        draft_transaction.delete()

        # Ensure that the intermediate files were deleted
        self.assertFalse(intermediate_file_path.exists())

        transaction = self.wallet.send_lovelace(
            lovelace_requested,
            to_address=to_address,
            password=DEFAULT_SPENDING_PASSWORD,
        )
        self.assertFalse(transaction._state.adding)
        self.assertFalse(transaction.intermediate_file_path.exists())

    def test_send_tokens(self):
        self.wallet.send_tokens(
            'd491fdc194c0d988459ce05a65c8a52259433e84d7162765570aa581.MMTestTokenTwo',
            1,
            to_address=self.wallet.payment_address,
        )

    def test_consolidate_utxos(self):
        self.wallet.consolidate_utxos(password=DEFAULT_SPENDING_PASSWORD)

    def test_partition_lovelace(self):
        min_value = 1000000
        max_value = 5000000
        values = [random.randint(min_value, max_value) for i in range(0, 10)]
        transaction = self.wallet.partition_lovelace(
            values=values,
            password=DEFAULT_SPENDING_PASSWORD
        )
        print(transaction.tx_id)

    def test_create_minting_policy(self):
        tip = CardanoUtils.query_tip()
        valid_before_slot = tip['slot'] + django_cardano_settings.DEFAULT_TRANSACTION_TTL

        minting_policy = MintingPolicy.objects.create(
            password=DEFAULT_SPENDING_PASSWORD,
            valid_before_slot=valid_before_slot,
        )
        policy_script_path = Path(minting_policy.script.path)
        self.assertTrue(policy_script_path.exists())

        # Scrap the generated policy script and associated keys
        shutil.rmtree(data_path_for_model(minting_policy))

    def test_mint_nft(self):
        def construct_nft_metadata(policy, asset_name):
            return {
                "721": {
                    policy.policy_id: {
                        asset_name: {
                            'name': 'django-cardano Test NFT',
                            'description': 'The Cardano Logo (in SVG format)',
                            'image': 'ipfs://QmS5gynkFMNFTnqPGgADxbvjutYmQK4Qh4iWwkSq4BhcmJ',

                        }
                    }
                }
            }

        transaction = self.wallet.mint_nft(
            asset_name="Test NFT",
            metadata=construct_nft_metadata,
            to_address=self.wallet.payment_address,
            spending_password=DEFAULT_SPENDING_PASSWORD,
            minting_password=DEFAULT_MINTING_PASSWORD,
        )
