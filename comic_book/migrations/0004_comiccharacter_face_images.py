from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("comic_book", "0003_comiccanvasnode"),
    ]

    operations = [
        migrations.AddField(
            model_name="comiccharacter",
            name="frontal_face_image_data_url",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="comiccharacter",
            name="sideways_face_image_data_url",
            field=models.TextField(blank=True, default=""),
        ),
    ]
