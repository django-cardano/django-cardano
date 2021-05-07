# Generated by Django 3.1.7 on 2021-05-07 23:42

from django.db import migrations, models
import django_cardano.fields
import django_cardano.models
import django_cardano.storage
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='MintingPolicy',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('policy_id', models.CharField(max_length=64)),
                ('script', models.FileField(max_length=200, storage=django_cardano.storage.CardanoDataStorage, upload_to=django_cardano.models.file_upload_path)),
                ('signing_key', models.FileField(max_length=200, storage=django_cardano.storage.CardanoDataStorage, upload_to=django_cardano.models.file_upload_path)),
                ('verification_key', models.FileField(max_length=200, storage=django_cardano.storage.CardanoDataStorage, upload_to=django_cardano.models.file_upload_path)),
            ],
            options={
                'swappable': 'DJANGO_CARDANO_MINTING_POLICY_MODEL',
            },
        ),
        migrations.CreateModel(
            name='Wallet',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(blank=True, max_length=30)),
                ('payment_address', django_cardano.fields.CardanoAddressField(max_length=200)),
                ('payment_signing_key', models.FileField(max_length=200, storage=django_cardano.storage.CardanoDataStorage, upload_to=django_cardano.models.file_upload_path)),
                ('payment_verification_key', models.FileField(max_length=200, storage=django_cardano.storage.CardanoDataStorage, upload_to=django_cardano.models.file_upload_path)),
                ('stake_address', django_cardano.fields.CardanoAddressField(max_length=200)),
                ('stake_signing_key', models.FileField(max_length=200, storage=django_cardano.storage.CardanoDataStorage, upload_to=django_cardano.models.file_upload_path)),
                ('stake_verification_key', models.FileField(max_length=200, storage=django_cardano.storage.CardanoDataStorage, upload_to=django_cardano.models.file_upload_path)),
            ],
            options={
                'abstract': False,
                'swappable': 'DJANGO_CARDANO_WALLET_MODEL',
            },
            managers=[
                ('objects', django_cardano.models.WalletManager()),
            ],
        ),
        migrations.CreateModel(
            name='Transaction',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('tx_id', models.CharField(blank=True, max_length=64, null=True)),
                ('inputs', models.JSONField(default=list)),
                ('outputs', models.JSONField(default=list)),
                ('metadata', models.JSONField(blank=True, null=True)),
                ('type', models.PositiveSmallIntegerField(choices=[(1, 'Lovelace Payment'), (2, 'Token Payment'), (3, 'Token Mint'), (4, 'Token Consolidation')])),
            ],
        ),
    ]
