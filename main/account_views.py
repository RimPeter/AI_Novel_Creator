from django.contrib import messages
from django.http import HttpResponseRedirect

from allauth.account.adapter import get_adapter
from allauth.account.views import EmailView

from .account_email import (
    REMOVE_EMAIL_REASON_NO_OTHER_VERIFIED,
    REMOVE_EMAIL_REASON_PRIMARY,
    assess_email_removal,
)


class ManagedEmailView(EmailView):
    def _action_remove(self, request, *args, **kwargs):
        email_address = self._get_email_address(request)
        if not email_address:
            return None

        can_remove, reason = assess_email_removal(email_address)
        if not can_remove:
            if reason == REMOVE_EMAIL_REASON_PRIMARY:
                get_adapter().add_message(
                    request,
                    messages.ERROR,
                    "account/messages/cannot_delete_primary_email.txt",
                    {"email": email_address.email},
                )
            elif reason == REMOVE_EMAIL_REASON_NO_OTHER_VERIFIED:
                messages.error(
                    request,
                    "You can only remove this email address when another verified email address remains on the account.",
                )
            return HttpResponseRedirect(self.get_success_url())

        return super()._action_remove(request, *args, **kwargs)
