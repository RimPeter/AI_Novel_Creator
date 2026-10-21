"""Per-user credentials. No deployment key fallback is permitted."""
from contextlib import contextmanager
from contextvars import ContextVar

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from .models import UserAICredential


current_ai_user = ContextVar("current_ai_user", default=None)
current_ai_request = ContextVar("current_ai_request", default=None)


def notify_fallback(message):
    request = current_ai_request.get()
    if request is None:
        return
    notices = getattr(request, "ai_fallback_notices", [])
    if message in notices:
        return
    notices.append(message)
    request.ai_fallback_notices = notices
    if "application/json" not in request.headers.get("Accept", "") and request.headers.get("X-Requested-With") != "XMLHttpRequest":
        from django.contrib import messages
        messages.warning(request, message)


class AIConfigurationError(ValueError):
    pass


def cipher():
    try:
        return Fernet(settings.AI_CREDENTIAL_ENCRYPTION_KEY.encode())
    except (AttributeError, ValueError):
        raise AIConfigurationError("API key storage is not configured. Contact the site administrator.") from None


@sensitive_variables()
def encrypt_key(key):
    return cipher().encrypt(key.encode()).decode()


@sensitive_variables()
def get_api_key(provider, user=None):
    user = user if user is not None else current_ai_user.get()
    if not user or not user.is_authenticated:
        raise AIConfigurationError("Sign in and connect your API key in AI Settings.")
    record = UserAICredential.objects.filter(user=user, provider=provider).first()
    if not record:
        label = "OpenAI" if provider == "openai" else "Claude"
        raise AIConfigurationError(f"Connect your {label} API key in AI Settings.")
    try:
        return cipher().decrypt(record.encrypted_key.encode()).decode()
    except InvalidToken:
        raise AIConfigurationError("Your API key could not be read. Reconnect it in AI Settings.") from None


def has_ai_connection(user):
    # A text selection may differ from the image provider. The adapter enforces
    # the exact provider for each operation before making any network call.
    return bool(user.is_authenticated and UserAICredential.objects.filter(user=user).exists())


@contextmanager
def ai_user_context(user):
    token = current_ai_user.set(user)
    try:
        yield
    finally:
        current_ai_user.reset(token)


class AICredentialMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = current_ai_request.set(request)
        try:
            with ai_user_context(request.user):
                response = self.get_response(request)
            if getattr(request, "ai_fallback_notices", None):
                import json
                response["X-AI-Fallback-Notices"] = json.dumps(request.ai_fallback_notices)
            return response
        finally:
            current_ai_request.reset(token)
