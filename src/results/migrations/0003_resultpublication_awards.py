"""Store the prize awards on the publication snapshot.

The prize configuration that produced them is already inside `inputs` and
covered by `input_digest`; these two fields hold the decision itself so
verify can recompute and compare it.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("results", "0002_resultpublication_feedback"),
    ]

    operations = [
        migrations.AddField(
            model_name="resultpublication",
            name="awards",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Prize awards as decided at publication time (results.prizes).",
            ),
        ),
        migrations.AddField(
            model_name="resultpublication",
            name="unawarded",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Prizes that could not be awarded, with the reason (ties need an organizer).",
            ),
        ),
    ]
