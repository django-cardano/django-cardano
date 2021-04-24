import os
from pathlib import Path

from django.test import TestCase

from .exceptions import CardanoError
from .models import Wallet
from .util import CardanoUtils

class DjangoCardanoTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.cardano = CardanoUtils()

        test_wallet_data_path = os.environ.get('TEST_WALLET_DATA_PATH')
        if test_wallet_data_path:
            if not os.path.exists(test_wallet_data_path):
                raise ValueError(f'Invalid wallet data path: {test_wallet_data_path}')
            cls.wallet = Wallet.objects.create_from_path(Path(test_wallet_data_path))

    def test_query_tip(self):
        tip_info = self.cardano.query_tip()

        self.assertIn('block', tip_info)
        self.assertIn('epoch', tip_info)
        self.assertIn('hash', tip_info)
        self.assertIn('slot', tip_info)

    def test_create_wallet(self):
        try:
            wallet = Wallet.objects.create(name='Test Wallet')

            address_info = wallet.payment_address_info
            self.assertEqual(address_info['type'], 'payment')
            self.assertEqual(address_info['encoding'], 'bech32')
            self.assertEqual(address_info['era'], 'shelley')
            self.assertEqual(address_info['address'], wallet.payment_address)
        except CardanoError as e:
            print(e)

    def test_get_wallet_info(self):
        wallet_info = self.wallet.info
        self.assertTrue(isinstance(wallet_info, dict))

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
        self.cardano.send_lovelace(
            4000000,
            from_wallet=self.wallet,
            to_address='addr_test1qrgf9v6zp884850vquxqw95zygp39xaxprfk4uzw5m9r4qlzvt0efu2dq9mmwp7v60wz5gsxz2d5vmewez5r7cf0c6vq0wlk3d',
        )

    def test_send_tokens(self):
        self.cardano.send_tokens(
            1,
            '6b8d07d69639e9413dd637a1a815a7323c69c86abbafb66dbfdb1aa7',
            from_wallet=self.wallet,
            to_address='addr_test1qrgf9v6zp884850vquxqw95zygp39xaxprfk4uzw5m9r4qlzvt0efu2dq9mmwp7v60wz5gsxz2d5vmewez5r7cf0c6vq0wlk3d',
        )

    def test_consolidate_tokens(self):
        self.cardano.consolidate_tokens(self.wallet)

    def test_mint_nft(self):
        nft_metadata = {

        }

        self.cardano.mint_nft(
            'MMTestToken',
            nft_metadata,
            from_wallet=self.wallet
        )
