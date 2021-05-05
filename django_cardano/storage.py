import os

from django.core.files.storage import FileSystemStorage


class CardanoDataStorage(FileSystemStorage):
    def __init__(self, *args, **kwargs):
        location = os.environ.get('CARDANO_APP_DATA_PATH')

        super().__init__(location, *args, **kwargs)
