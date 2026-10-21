from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0030_allow_verified_testing_email_duplicates"),
    ]

    operations = [
        migrations.CreateModel(
            name="SignupConfiguration",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("singleton_key", models.PositiveSmallIntegerField(default=1, editable=False, unique=True)),
                ("allow_signups", models.BooleanField(default=True)),
            ],
            options={
                "verbose_name": "Signup configuration",
                "verbose_name_plural": "Signup configuration",
            },
        ),
    ]
