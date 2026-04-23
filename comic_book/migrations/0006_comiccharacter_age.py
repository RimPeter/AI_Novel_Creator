from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("comic_book", "0005_comiccharacter_full_body_image"),
    ]

    operations = [
        migrations.AddField(
            model_name="comiccharacter",
            name="age",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
