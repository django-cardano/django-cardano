import json

from django.core.management.base import BaseCommand

from django_cardano import Cardano, CardanoError

from django_cardano.models import Wallet

class Command(BaseCommand):
    def handle(self, *args, **options):
        cardano = Cardano()

        wallet = cardano.create_wallet()
        print(wallet.vkey)


