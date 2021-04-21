from django.core import management
from django.test import TestCase

from django_cardano import Cardano, CardanoError


class DjangoCardanoTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.cardano = Cardano()

    def test_query_tip(self):
        try:
            tip = self.cardano.query_tip()
            print(tip)
        except CardanoError as e:
            print(e.reason)
