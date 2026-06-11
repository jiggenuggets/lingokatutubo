from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("translator", "0002_translationjob_upload_file_path"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="translationjob",
            old_name="translator__owner_i_940c04_idx",
            new_name="translator__owner_i_9100f4_idx",
        ),
        migrations.RenameIndex(
            model_name="translationjob",
            old_name="translator__status_0ee30e_idx",
            new_name="translator__status_f126c5_idx",
        ),
    ]
