from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("translator", "0005_alter_translationjob_file_type_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="translationjob",
            name="is_deleted",
            field=models.BooleanField(default=False, db_index=True),
        ),
        migrations.AddField(
            model_name="translationjob",
            name="deleted_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddIndex(
            model_name="translationjob",
            index=models.Index(
                fields=["owner", "is_deleted", "-created_at"],
                name="trans_owner_del_created_idx",
            ),
        ),
    ]
