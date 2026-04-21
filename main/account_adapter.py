from django.contrib import messages

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.internal.flows.manage_email import emit_email_changed
from allauth.account.models import EmailAddress

from .account_email import assess_email_removal, is_multi_account_test_email


class AccountAdapter(DefaultAccountAdapter):
    def can_delete_email(self, email_address) -> bool:
        can_remove, _reason = assess_email_removal(email_address)
        return can_remove

    def confirm_email(self, request, email_address):
        if not is_multi_account_test_email(email_address.email):
            return super().confirm_email(request, email_address)

        from allauth.account import app_settings, signals

        added = not email_address.pk
        from_email_address = (
            EmailAddress.objects.filter(user_id=email_address.user_id)
            .exclude(pk=email_address.pk)
            .first()
        )
        if not email_address.verified:
            email_address.verified = True
        email_address.set_as_primary(conditional=(not app_settings.CHANGE_EMAIL))
        email_address.save()
        if added:
            signals.email_added.send(
                sender=EmailAddress,
                request=request,
                user=request.user,
                email_address=email_address,
            )
        signals.email_confirmed.send(
            sender=EmailAddress,
            request=request,
            email_address=email_address,
        )
        if app_settings.CHANGE_EMAIL:
            for instance in EmailAddress.objects.filter(user_id=email_address.user_id).exclude(pk=email_address.pk):
                instance.remove()
            emit_email_changed(request, from_email_address, email_address)
        self.add_message(
            request,
            messages.SUCCESS,
            "account/messages/email_confirmed.txt",
            {"email": email_address.email},
        )
        return True
