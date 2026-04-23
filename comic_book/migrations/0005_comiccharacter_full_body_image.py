from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("comic_book", "0004_comiccharacter_face_images"),
    ]

    operations = [
        migrations.AddField(
            model_name="comiccharacter",
            name="full_body_image_data_url",
            field=models.TextField(blank=True, default=""),
        ),
    ]
