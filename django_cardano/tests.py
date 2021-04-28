import os
import uuid
from pathlib import Path

from django.test import TestCase

from .exceptions import CardanoError
from .models import MintingPolicy, get_wallet_model
from .util import CardanoUtils

Wallet = get_wallet_model()


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
        wallet_info = self.wallet.payment_address_info
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
        self.wallet.send_lovelace(
            1000000,
            to_address='addr_test1qrw7nlpnda79j0we7supfpzqfepcl2tncppla23k0pk8p5jrhjed4h58xc3d2ghuj2y24q9l0gz40y0w92a6z6zp3eqqvtjgr7',
        )

    def test_send_tokens(self):
        self.wallet.send_tokens(
            1,
            'd491fdc194c0d988459ce05a65c8a52259433e84d7162765570aa581.MMTestTokenTwo',
            to_address='addr_test1qrgf9v6zp884850vquxqw95zygp39xaxprfk4uzw5m9r4qlzvt0efu2dq9mmwp7v60wz5gsxz2d5vmewez5r7cf0c6vq0wlk3d',
        )

    def test_consolidate_utxos(self):
        self.wallet.consolidate_utxos()

    def test_mint_nft(self):
        minting_policy = MintingPolicy.objects.create()

        metadata = {
            'name': 'MintMachine Test NFT',
            'description': 'An image that _should_ exist in perpetuity',
            'image': 'https://i.imgur.com/6zJM4Eh.png',
            'ticker': 'MINTMACHINE'
        }
        self.wallet.mint_nft(
            minting_policy,
            str(uuid.uuid4()),
            metadata,
            to_address='addr_test1qrgf9v6zp884850vquxqw95zygp39xaxprfk4uzw5m9r4qlzvt0efu2dq9mmwp7v60wz5gsxz2d5vmewez5r7cf0c6vq0wlk3d',
        )
