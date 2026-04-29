from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("comic_book", "0008_comiclocation_image_data_url"),
    ]

    operations = [
        migrations.RenameField(
            model_name="comicpage",
            old_name="canvas_layout",
            new_name="panel_layout",
        ),
        migrations.RemoveConstraint(
            model_name="comiccanvasnode",
            name="uniq_comic_canvas_node_page_key",
        ),
        migrations.RenameField(
            model_name="comiccanvasnode",
            old_name="canvas_key",
            new_name="panel_key",
        ),
        migrations.RenameModel(
            old_name="ComicCanvasNode",
            new_name="ComicPanelNode",
        ),
        migrations.AlterField(
            model_name="comicpanelnode",
            name="location",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="panel_nodes",
                to="comic_book.comiclocation",
            ),
        ),
        migrations.AlterField(
            model_name="comicpanelnode",
            name="page",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="panel_nodes",
                to="comic_book.comicpage",
            ),
        ),
        migrations.AlterField(
            model_name="comicpanelnode",
            name="parent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="children",
                to="comic_book.comicpanelnode",
            ),
        ),
        migrations.AlterField(
            model_name="comicpanelnode",
            name="characters",
            field=models.ManyToManyField(blank=True, related_name="panel_nodes", to="comic_book.comiccharacter"),
        ),
        migrations.AddConstraint(
            model_name="comicpanelnode",
            constraint=models.UniqueConstraint(fields=("page", "panel_key"), name="uniq_comic_panel_node_page_key"),
        ),
    ]
