from django.db import migrations


TEST_EMAIL = "primaszecsi@gmail.com"


def _drop_unique_verified_email(schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "sqlite":
        schema_editor.execute("DROP INDEX IF EXISTS unique_verified_email;")
    elif vendor == "postgresql":
        schema_editor.execute("DROP INDEX IF EXISTS unique_verified_email;")


def _create_unique_verified_email(schema_editor, *, exclude_test_email):
    vendor = schema_editor.connection.vendor
    exclusion = ""
    if exclude_test_email:
        exclusion = f" AND email <> '{TEST_EMAIL}'"
    if vendor == "sqlite":
        schema_editor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS unique_verified_email "
            f"ON account_emailaddress (email) WHERE verified{exclusion};"
        )
    elif vendor == "postgresql":
        schema_editor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS unique_verified_email "
            f"ON account_emailaddress (email) WHERE verified{exclusion};"
        )


def forwards(apps, schema_editor):
    _drop_unique_verified_email(schema_editor)
    _create_unique_verified_email(schema_editor, exclude_test_email=True)


def backwards(apps, schema_editor):
    _drop_unique_verified_email(schema_editor)
    _create_unique_verified_email(schema_editor, exclude_test_email=False)


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0029_scenecriticreview"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
