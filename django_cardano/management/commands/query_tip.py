from django.core.management.base import BaseCommand

from django_cardano.settings import django_cardano_settings

class Command(BaseCommand):
    def handle(self, *args, **options):
        print(django_cardano_settings.NETWORK)
