from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0024_billingcompanyprofile"),
    ]

    operations = [
        migrations.AlterField(
            model_name="novelproject",
            name="slug",
            field=models.SlugField(max_length=120),
        ),
        migrations.AddConstraint(
            model_name="novelproject",
            constraint=models.UniqueConstraint(fields=("owner", "slug"), name="uniq_project_owner_slug"),
        ),
    ]
