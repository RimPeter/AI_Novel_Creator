from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0025_novelproject_owner_slug_constraint"),
    ]

    operations = [
        migrations.AddField(
            model_name="homeupdate",
            name="source_key",
            field=models.CharField(blank=True, db_index=True, default="", max_length=120),
        ),
        migrations.AddConstraint(
            model_name="homeupdate",
            constraint=models.UniqueConstraint(
                condition=models.Q(("source_key", ""), _negated=True),
                fields=("source_key",),
                name="uniq_homeupdate_source_key_nonempty",
            ),
        ),
    ]
