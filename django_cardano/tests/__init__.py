import os
import shutil
import uuid
from pathlib import Path

from django.test import TestCase

from ..exceptions import CardanoError
from ..models import (
    get_wallet_model,
    MintingPolicy,
    Transaction,
)
from ..util import CardanoUtils

Wallet = get_wallet_model()

DEFAULT_WALLET_PASSWORD = 'fL;$qR9FZ3?stf-M'


class DjangoCardanoTestCase(TestCase):
    cardano = CardanoUtils()
    wallet = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        CWD = Path(__file__).resolve().parent
        cls.wallet = Wallet.objects.create_from_path(CWD / 'data')


    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()

        # Discard the associated key files
        shutil.rmtree(cls.wallet.data_path)


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

        draft_transaction, tx_fee = self.wallet.send_lovelace(
            lovelace_requested,
            to_address=to_address,
        )
        self.assertTrue(isinstance(draft_transaction, Transaction))
        self.assertTrue(isinstance(tx_fee, int))
        self.assertTrue(draft_transaction._state.adding)

        transaction, tx_fee = self.wallet.send_lovelace(
            lovelace_requested,
            to_address=to_address,
            password=DEFAULT_WALLET_PASSWORD,
        )
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
        self.wallet.consolidate_utxos()

    def test_create_minting_policy(self):
        minting_policy = MintingPolicy.objects.create(password=DEFAULT_WALLET_PASSWORD)
        policy_script_path = minting_policy.script.url
        self.assertTrue(os.path.exists(policy_script_path))

        # Scrap the generated policy script and associated keys
        shutil.rmtree(minting_policy.data_path)

    def test_mint_nft(self):
        minting_policy = MintingPolicy.objects.create(password=DEFAULT_WALLET_PASSWORD)

        metadata = {
            'name': 'MintMachine Test NFT',
            'description': 'An image that _should_ exist in perpetuity',
            'image': 'https://i.imgur.com/6zJM4Eh.png',
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
        shutil.rmtree(minting_policy.data_path)

