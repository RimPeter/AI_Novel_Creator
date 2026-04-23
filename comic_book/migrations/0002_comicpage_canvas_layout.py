from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("comic_book", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="comicpage",
            name="canvas_layout",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
