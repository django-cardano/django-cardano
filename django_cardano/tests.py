from django.core import management
from django.test import TestCase

from django_cardano import CardanoTools

class DjangoCardanoTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.cardano_tools = CardanoTools()

    def test_query_tip(self):
        print(self.cardano_tools.query_tip())
