from allauth.account.models import EmailAddress

MULTI_ACCOUNT_TEST_EMAIL = "primaszecsi@gmail.com"

REMOVE_EMAIL_REASON_PRIMARY = "primary"
REMOVE_EMAIL_REASON_NO_OTHER_VERIFIED = "no_other_verified"
REMOVE_EMAIL_REASON_ALLOWED = "allowed"


def is_multi_account_test_email(email):
    return (str(email or "").strip().lower() == MULTI_ACCOUNT_TEST_EMAIL)


def assess_email_removal(email_address):
    if not email_address.pk:
        return True, REMOVE_EMAIL_REASON_ALLOWED

    if email_address.primary:
        return False, REMOVE_EMAIL_REASON_PRIMARY

    has_other_verified = (
        EmailAddress.objects.filter(user_id=email_address.user_id, verified=True)
        .exclude(pk=email_address.pk)
        .exists()
    )
    if not has_other_verified:
        return False, REMOVE_EMAIL_REASON_NO_OTHER_VERIFIED

    return True, REMOVE_EMAIL_REASON_ALLOWED
