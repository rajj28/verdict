"""Make Event.shrinkage_lambda nullable (null = 'auto' λ selection).

Null means the engine will use 5-fold cross-validation to choose λ from
the default grid (BUILD-SPEC section 19). Existing events are set to null
so they also benefit from adaptive selection; organizers can still pre-declare
a fixed λ before the scoring lock.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="event",
            name="shrinkage_lambda",
            field=models.DecimalField(
                max_digits=5,
                decimal_places=2,
                null=True,
                blank=True,
                default=None,
                help_text="Shrinkage penalty λ; null = auto (5-fold CV).",
            ),
        ),
        # Existing rows get null so they also use adaptive selection.
        migrations.RunSQL(
            sql="UPDATE events_event SET shrinkage_lambda = NULL;",
            reverse_sql="UPDATE events_event SET shrinkage_lambda = 2.00;",
        ),
    ]
