from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0026_homeupdate_source_key"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BillingInformationProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("first_name", models.CharField(blank=True, default="", max_length=120)),
                ("last_name", models.CharField(blank=True, default="", max_length=120)),
                ("company_name", models.CharField(blank=True, default="", max_length=255)),
                ("email", models.CharField(blank=True, default="", max_length=255)),
                ("address_line_1", models.CharField(blank=True, default="", max_length=255)),
                ("address_line_2", models.CharField(blank=True, default="", max_length=255)),
                ("city", models.CharField(blank=True, default="", max_length=120)),
                ("state_region", models.CharField(blank=True, default="", max_length=120)),
                ("postcode", models.CharField(blank=True, default="", max_length=40)),
                ("country", models.CharField(blank=True, default="", max_length=120)),
                ("tax_id", models.CharField(blank=True, default="", max_length=120)),
                ("is_business_purchase", models.BooleanField(default=False)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="billing_information_profile", to=settings.AUTH_USER_MODEL)),
            ],
            options={},
        ),
        migrations.AddField(
            model_name="billinginvoice",
            name="buyer_company_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="billinginvoice",
            name="buyer_tax_id",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
    ]
