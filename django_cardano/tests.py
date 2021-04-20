from django.core import management
from django.test import TestCase


class AnimalTestCase(TestCase):
    def test_query_tip(self):
        management.call_command('query_tip')
