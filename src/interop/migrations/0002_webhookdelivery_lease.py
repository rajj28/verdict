from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("interop", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="webhookdelivery",
            name="lease_token",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="webhookdelivery",
            name="lease_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
