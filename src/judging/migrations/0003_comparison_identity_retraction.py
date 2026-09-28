from django.db import migrations, models
import core.clock
import judging.models


def identify_comparisons(apps, schema_editor):
    Comparison = apps.get_model("judging", "Comparison")
    for comparison in Comparison.objects.order_by("id").iterator():
        comparison.public_id = judging.models._new_cmp_id()
        comparison.left_id, comparison.right_id = sorted((comparison.left_id, comparison.right_id))
        comparison.save(update_fields=["public_id", "left", "right"])


class Migration(migrations.Migration):
    dependencies = [("judging", "0002_alter_assignmentbatch_method")]
    operations = [
        migrations.AddField(model_name="comparison", name="public_id",
                            field=models.CharField(max_length=32, null=True)),
        migrations.AddField(model_name="comparison", name="retracted_at",
                            field=models.DateTimeField(blank=True, null=True)),
        migrations.RunPython(identify_comparisons, migrations.RunPython.noop),
        migrations.AlterField(model_name="comparison", name="public_id",
                              field=models.CharField(default=judging.models._new_cmp_id,
                                                     max_length=32, unique=True)),
        migrations.AlterField(model_name="comparison", name="created_at",
                              field=models.DateTimeField(default=core.clock.now, editable=False)),
        migrations.AddConstraint(model_name="comparison", constraint=models.CheckConstraint(
            condition=models.Q(left__lt=models.F("right")), name="comparison_canonical_pair")),
        migrations.AddConstraint(model_name="comparison", constraint=models.CheckConstraint(
            condition=models.Q(winner__isnull=True) | models.Q(winner=models.F("left"))
            | models.Q(winner=models.F("right")), name="comparison_winner_in_pair")),
        migrations.AddConstraint(model_name="comparison", constraint=models.UniqueConstraint(
            fields=("judge", "left", "right"), condition=models.Q(retracted_at__isnull=True),
            name="comparison_unique_active_pair")),
    ]
