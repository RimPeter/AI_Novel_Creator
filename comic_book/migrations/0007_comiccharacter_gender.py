from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("comic_book", "0006_comiccharacter_age"),
    ]

    operations = [
        migrations.AddField(
            model_name="comiccharacter",
            name="gender",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
    ]
