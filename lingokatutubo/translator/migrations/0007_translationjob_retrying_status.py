from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("translator", "0006_translationjob_soft_delete"),
    ]

    operations = [
        migrations.AlterField(
            model_name="translationjob",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("retrying", "Retrying"),
                    ("processing", "Processing"),
                    ("completed", "Completed"),
                    ("failed", "Failed"),
                ],
                db_index=True,
                default="queued",
                max_length=24,
            ),
        ),
    ]
