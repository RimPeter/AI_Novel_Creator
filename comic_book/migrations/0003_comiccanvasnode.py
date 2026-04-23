from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ("comic_book", "0002_comicpage_canvas_layout"),
    ]

    operations = [
        migrations.CreateModel(
            name="ComicCanvasNode",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("canvas_key", models.CharField(max_length=120)),
                ("node_type", models.CharField(choices=[("PANEL", "Panel"), ("SPLIT", "Split")], default="PANEL", max_length=10)),
                ("child_index", models.PositiveSmallIntegerField(default=0)),
                ("split_direction", models.CharField(blank=True, choices=[("horizontal", "Horizontal"), ("vertical", "Vertical")], default="", max_length=20)),
                ("split_ratio", models.DecimalField(blank=True, decimal_places=3, max_digits=4, null=True)),
                ("focus", models.CharField(blank=True, default="", max_length=160)),
                ("action", models.TextField(blank=True, default="")),
                ("shot_type", models.CharField(choices=[("FULL", "Full"), ("WIDE", "Wide"), ("MEDIUM", "Medium"), ("CLOSE", "Close"), ("EXTREME_CLOSE", "Extreme close"), ("INSERT", "Insert")], default="MEDIUM", max_length=20)),
                ("camera_angle", models.CharField(blank=True, default="", max_length=120)),
                ("mood", models.CharField(blank=True, default="", max_length=120)),
                ("lighting_notes", models.TextField(blank=True, default="")),
                ("dialogue_space", models.CharField(blank=True, default="", max_length=120)),
                ("must_include", models.TextField(blank=True, default="")),
                ("must_avoid", models.TextField(blank=True, default="")),
                ("style_override", models.TextField(blank=True, default="")),
                ("notes", models.TextField(blank=True, default="")),
                ("image_prompt", models.TextField(blank=True, default="")),
                ("image_data_url", models.TextField(blank=True, default="")),
                ("image_status", models.CharField(choices=[("IDLE", "Idle"), ("READY", "Ready"), ("FAILED", "Failed")], default="IDLE", max_length=12)),
                ("last_generated_at", models.DateTimeField(blank=True, null=True)),
                (
                    "location",
                    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="canvas_nodes", to="comic_book.comiclocation"),
                ),
                (
                    "page",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="canvas_nodes", to="comic_book.comicpage"),
                ),
                (
                    "parent",
                    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="children", to="comic_book.comiccanvasnode"),
                ),
            ],
            options={
                "ordering": ["created_at", "id"],
            },
        ),
        migrations.AddField(
            model_name="comiccanvasnode",
            name="characters",
            field=models.ManyToManyField(blank=True, related_name="canvas_nodes", to="comic_book.comiccharacter"),
        ),
        migrations.AddConstraint(
            model_name="comiccanvasnode",
            constraint=models.UniqueConstraint(fields=("page", "canvas_key"), name="uniq_comic_canvas_node_page_key"),
        ),
    ]
