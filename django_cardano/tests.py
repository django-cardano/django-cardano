import os
import subprocess
from pathlib import Path

from django.test import TestCase

from django_cardano import Cardano, CardanoError
from django_cardano.models import Wallet


class DjangoCardanoTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.cardano = Cardano()

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

            address_info = self.cardano.address_info(wallet.payment_address)
            self.assertEqual(address_info['type'], 'payment')
            self.assertEqual(address_info['encoding'], 'bech32')
            self.assertEqual(address_info['era'], 'shelley')
            self.assertEqual(address_info['address'], wallet.payment_address)
        except CardanoError as e:
            print(e)

    def test_get_utxos(self):
        utxos = self.cardano.query_utxos(self.wallet.payment_address)
        for utxo in utxos:
            self.assertIn('TxHash', utxo)
            self.assertIn('TxIx', utxo)
            self.assertIn('Assets', utxo)

    def test_get_lovelace_balance(self):
        balance = self.cardano.query_lovelace_balance(self.wallet.payment_address)
        self.assertTrue(isinstance(balance, int))

    def test_send_lovelace(self):
        response = self.cardano.send_lovelace(
            5000000,
            from_wallet=self.wallet,
            to_address='addr_test1qrgf9v6zp884850vquxqw95zygp39xaxprfk4uzw5m9r4qlzvt0efu2dq9mmwp7v60wz5gsxz2d5vmewez5r7cf0c6vq0wlk3d',
        )
        print(response)

    def test_mint_nft(self):
        try:
            self.cardano.mint_nft(
                asset_name='MMTestToken',
                payment_wallet=self.wallet
            )
            print('woohoo!')
        except CardanoError as e:
            print(e.reason)
