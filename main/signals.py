from typing import Optional

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_migrate
from django.dispatch import receiver


def sync_site_configuration():
    Site = django_apps.get_model("sites", "Site")
    Site.objects.update_or_create(
        id=settings.SITE_ID,
        defaults={
            "domain": settings.SITE_DOMAIN,
            "name": settings.SITE_NAME,
        },
    )


def sync_legacy_account_emails(*, user=None, email: Optional[str] = None):
    EmailAddress = django_apps.get_model("account", "EmailAddress")
    User = get_user_model()

    users = User._default_manager.exclude(email__isnull=True).exclude(email="")
    if user is not None:
        users = users.filter(pk=user.pk)
    if email:
        users = users.filter(email__iexact=email.strip())

    for user in users.iterator():
        matching_address = (
            EmailAddress.objects.filter(user=user, email__iexact=user.email)
            .order_by("-primary", "-verified", "id")
            .first()
        )

        if matching_address is None:
            EmailAddress.objects.filter(user=user, primary=True).update(primary=False)
            EmailAddress.objects.create(
                user=user,
                email=user.email,
                primary=True,
                verified=True,
            )
            continue

        updates = []
        if not matching_address.primary:
            EmailAddress.objects.filter(user=user, primary=True).exclude(pk=matching_address.pk).update(primary=False)
            matching_address.primary = True
            updates.append("primary")
        if not matching_address.verified:
            matching_address.verified = True
            updates.append("verified")

        if updates:
            matching_address.save(update_fields=updates)


@receiver(post_migrate)
def sync_legacy_account_emails_after_migrate(sender, **kwargs):
    if sender.label != "main":
        return
    sync_site_configuration()
    sync_legacy_account_emails()
