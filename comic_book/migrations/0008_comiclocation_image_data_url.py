from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("comic_book", "0007_comiccharacter_gender"),
    ]

    operations = [
        migrations.AddField(
            model_name="comiclocation",
            name="image_data_url",
            field=models.TextField(blank=True, default=""),
        ),
    ]
