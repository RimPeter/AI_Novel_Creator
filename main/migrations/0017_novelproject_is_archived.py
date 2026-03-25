from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0016_homeupdate"),
    ]

    operations = [
        migrations.AddField(
            model_name="novelproject",
            name="is_archived",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
