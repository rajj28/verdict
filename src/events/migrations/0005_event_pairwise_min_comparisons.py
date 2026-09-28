from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("events", "0004_alter_event_shrinkage_lambda")]
    operations = [
        migrations.AddField(model_name="event", name="pairwise_min_comparisons",
                            field=models.PositiveSmallIntegerField(default=3)),
        migrations.AddConstraint(model_name="event", constraint=models.CheckConstraint(
            condition=models.Q(pairwise_min_comparisons__gte=1), name="event_pairwise_min_positive")),
    ]
