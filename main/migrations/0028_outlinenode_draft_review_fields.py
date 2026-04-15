from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0027_billinginformationprofile_billinginvoice_buyer_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="outlinenode",
            name="draft_review_data",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="outlinenode",
            name="draft_review_fingerprint",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="outlinenode",
            name="draft_review_generated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="outlinenode",
            name="draft_review_model_name",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
    ]
