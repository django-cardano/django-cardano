from django.core import management
from django.test import TestCase

from django_cardano import Cardano, CardanoError


class DjangoCardanoTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.cardano = Cardano()

    def test_query_tip(self):
        tip_info = self.cardano.query_tip()

        self.assertIn('block', tip_info)
        self.assertIn('epoch', tip_info)
        self.assertIn('hash', tip_info)
        self.assertIn('slot', tip_info)

    def test_create_wallet(self):
        try:
            wallet = self.cardano.create_wallet(name='Test Wallet')
            print(wallet.payment_signing_key)
            print(wallet.payment_address)
        except CardanoError as e:
            print(e)
