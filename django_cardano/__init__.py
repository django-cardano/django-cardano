from django_cardano.settings import django_cardano_settings


class CardanoTools:
    def query_tip(self):
        print(django_cardano_settings.NETWORK)
        return 'abc123'
