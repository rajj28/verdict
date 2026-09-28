import django.db.models.deletion
from django.db import migrations, models

import core.models


class Migration(migrations.Migration):
    initial = True
    dependencies = [("events", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="OutboxMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.CharField(default=core.models._new_outbox_id, max_length=32, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("recipients", models.JSONField(default=list)),
                ("subject", models.TextField()),
                ("body", models.TextField()),
                ("from_email", models.EmailField(max_length=320)),
                ("event", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                             related_name="outbox_messages", to="events.event")),
            ],
            options={
                "ordering": ["-created_at", "-pk"],
                "indexes": [models.Index(fields=["event", "created_at"], name="core_outbox_event_created")],
            },
        ),
    ]
