"""Add feedback_released_at and feedback_released_by to ResultPublication."""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("results", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="resultpublication",
            name="feedback_released_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="When set, team members can see their project's de-attributed feedback.",
            ),
        ),
        migrations.AddField(
            model_name="resultpublication",
            name="feedback_released_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="feedback_releases",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
