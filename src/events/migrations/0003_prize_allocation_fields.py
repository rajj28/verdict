"""Prize allocation fields: scope, places, eligibility note, one-prize-per-team.

`results.prizes` allocates awards from these fields, so they are part of the
scoring configuration and end up in every publication's input digest.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0002_event_shrinkage_lambda_nullable"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="one_prize_per_team",
            field=models.BooleanField(
                default=True,
                help_text="A team already awarded a higher prize is skipped for lower ones.",
            ),
        ),
        migrations.AddField(
            model_name="prize",
            name="scope",
            field=models.CharField(
                choices=[("overall", "Overall (any track)"), ("track", "Track")],
                default="overall",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="prize",
            name="places",
            field=models.PositiveSmallIntegerField(
                default=1,
                help_text="How many ranked places this prize awards (1 = single winner).",
            ),
        ),
        migrations.AddField(
            model_name="prize",
            name="eligibility_note",
            field=models.CharField(
                blank=True,
                help_text="Shown with the award, e.g. 'must ship a running demo'.",
                max_length=300,
            ),
        ),
        migrations.AlterField(
            model_name="prize",
            name="scope",
            field=models.CharField(
                choices=[("overall", "Overall (any track)"), ("track", "Track")],
                default="overall",
                max_length=8,
                help_text="Overall prizes draw from every ranked project; track prizes only from their track.",
            ),
        ),
        # Existing prizes that already carry a track are track prizes.
        migrations.RunSQL(
            sql="UPDATE events_prize SET scope = 'track' WHERE track_id IS NOT NULL;",
            reverse_sql="UPDATE events_prize SET scope = 'overall' WHERE track_id IS NOT NULL;",
        ),
        migrations.AddConstraint(
            model_name="prize",
            constraint=models.CheckConstraint(
                condition=models.Q(places__gte=1),
                name="prize_places_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="prize",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(scope="overall", track__isnull=True)
                    | models.Q(scope="track", track__isnull=False)
                ),
                name="prize_scope_matches_track",
            ),
        ),
    ]
