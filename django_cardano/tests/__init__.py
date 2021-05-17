import os
import random
import shutil
import uuid
from pathlib import Path

from django.test import TestCase
from django.utils.text import slugify

from ..exceptions import CardanoError
from ..models import (
    get_wallet_model,
    MintingPolicy,
    Transaction,
)
from ..util import CardanoUtils

Wallet = get_wallet_model()

DEFAULT_WALLET_PASSWORD = 'fL;$qR9FZ3?stf-M'


def data_path_for_model(instance):
    base_path = os.environ.get('CARDANO_APP_DATA_PATH')
    model_name = slugify(instance._meta.verbose_name)
    return Path(base_path, model_name, str(instance.id))


class DjangoCardanoTestCase(TestCase):
    cardano = CardanoUtils()
    wallet = None

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cwd = Path(__file__).resolve().parent
        cls.wallet = Wallet.objects.create_from_path(cwd / 'data')

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()

        # Discard the associated key files
        shutil.rmtree(data_path_for_model(cls.wallet))

    def test_query_tip(self):
        tip_info = self.cardano.query_tip()

        self.assertIn('block', tip_info)
        self.assertIn('epoch', tip_info)
        self.assertIn('hash', tip_info)
        self.assertIn('slot', tip_info)

    def test_create_wallet(self):
        try:
            wallet = Wallet.objects.create(
                name='Test Wallet',
                password=DEFAULT_WALLET_PASSWORD
            )

            address_info = self.cardano.address_info(wallet.payment_address)
            self.assertEqual(address_info['type'], 'payment')
            self.assertEqual(address_info['encoding'], 'bech32')
            self.assertEqual(address_info['era'], 'shelley')
            self.assertEqual(address_info['address'], wallet.payment_address)

            shutil.rmtree(data_path_for_model(wallet))
        except CardanoError as e:
            print(e)

    def test_get_address_info(self):
        address_info = self.cardano.address_info(self.wallet.payment_address)
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

        transaction = self.wallet.send_lovelace(
            lovelace_requested,
            to_address=to_address,
            password=DEFAULT_WALLET_PASSWORD,
        )
        tx_fee = draft_transaction.calculate_min_fee()
        self.assertTrue(isinstance(draft_transaction, Transaction))
        self.assertTrue(isinstance(tx_fee, int))
        self.assertFalse(transaction._state.adding)

    def test_send_tokens(self):
        self.wallet.send_tokens(
            'd491fdc194c0d988459ce05a65c8a52259433e84d7162765570aa581.MMTestTokenTwo',
            1,
            to_address=self.wallet.payment_address,
        )

    def test_consolidate_utxos(self):
        self.wallet.consolidate_utxos(password=DEFAULT_WALLET_PASSWORD)

    def test_partition_lovelace(self):
        min_value = 1000000
        max_value = 5000000
        values = [random.randint(min_value, max_value) for i in range(0, 10)]
        transaction = self.wallet.partition_lovelace(
            values=values,
            password=DEFAULT_WALLET_PASSWORD
        )
        print(transaction.tx_id)

    def test_create_minting_policy(self):
        minting_policy = MintingPolicy.objects.create(password=DEFAULT_WALLET_PASSWORD)
        policy_script_path = Path(minting_policy.script.path)
        self.assertTrue(policy_script_path.exists())

        # Scrap the generated policy script and associated keys
        shutil.rmtree(data_path_for_model(minting_policy))

    def test_mint_nft(self):
        minting_policy = MintingPolicy.objects.create(password=DEFAULT_WALLET_PASSWORD)

        metadata = {
            'name': 'django-cardano Test NFT',
            'description': 'The Cardano Logo (in SVG format)',
            'image': 'ipfs://QmS5gynkFMNFTnqPGgADxbvjutYmQK4Qh4iWwkSq4BhcmJ',
        }

        self.wallet.mint_nft(
            minting_policy,
            asset_name=str(uuid.uuid4()),
            metadata=metadata,
            to_address=self.wallet.payment_address,
            spending_password=DEFAULT_WALLET_PASSWORD,
            minting_password=DEFAULT_WALLET_PASSWORD,
        )

        # Scrap the generated policy script and associated keys
        shutil.rmtree(data_path_for_model(minting_policy))

        minting_policy.delete()
