"""Provider transport, with bounded requests and safe public errors."""
from contextvars import ContextVar

import httpx
from openai import OpenAI, APIError, APIConnectionError
from django.views.decorators.debug import sensitive_variables

from .ai_credentials import AIConfigurationError, get_api_key

request_timeout = ContextVar("ai_request_timeout", default=120)


class ProviderRequestError(AIConfigurationError):
    def __init__(self, message, *, kind="request"):
        super().__init__(message)
        self.kind = kind


def provider_error(status, code=None):
    if status == 402 or code in ("insufficient_quota", "billing_hard_limit_reached", "credit_balance_too_low"):
        return ProviderRequestError("Your provider quota was exhausted. Check your provider account.", kind="quota")
    if status == 429:
        return ProviderRequestError("Your provider quota or rate limit was reached. Try again later.", kind="rate_limit")
    if status is not None and status >= 500:
        return ProviderRequestError("The AI provider is temporarily unavailable. Please try again.", kind="server")
    if status in (401, 403):
        return AIConfigurationError("The provider rejected your API key or permissions. Check AI Settings.")
    if status == 404:
        return AIConfigurationError("This model is unavailable for your API key. Choose another model in AI Settings.")
    return AIConfigurationError("The AI provider could not complete the request. Check your model settings and try again.")


@sensitive_variables()
def openai_request(resource, **kwargs):
    key = get_api_key("openai")
    try:
        with OpenAI(api_key=key, base_url="https://api.openai.com/v1", organization="", project="", timeout=request_timeout.get(), max_retries=0) as client:
            method = client
            for part in resource.split("."):
                method = getattr(method, part)
            return method(**kwargs)
    except APIError as exc:
        if isinstance(exc, APIConnectionError):
            raise ProviderRequestError("Could not connect to the AI provider. Please try again.", kind="connection") from None
        raise provider_error(getattr(exc, "status_code", None), getattr(exc, "code", None)) from None


@sensitive_variables()
def anthropic_request(path, *, key=None, payload=None):
    key = key or get_api_key("anthropic")
    try:
        with httpx.Client(timeout=request_timeout.get()) as client:
            response = client.request(
                "POST" if payload is not None else "GET",
                "https://api.anthropic.com/v1/" + path,
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                **({"json": payload} if payload is not None else {}),
            )
            if response.status_code >= 400:
                try:
                    error = response.json().get("error", {}) if response.status_code == 400 else {}
                    code = error.get("type")
                    if "credit balance is too low" in str(error.get("message", "")).lower():
                        code = "credit_balance_too_low"
                except (ValueError, AttributeError, TypeError):
                    code = None
                raise provider_error(response.status_code, code)
            return response.json()
    except httpx.TransportError:
        raise ProviderRequestError("Could not connect to the AI provider. Please try again.", kind="connection") from None
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, AIConfigurationError):
            raise
        raise provider_error(None) from None


@sensitive_variables()
def validate_key(provider, key):
    # Listing models validates credentials without generating billable content.
    if provider == "anthropic":
        anthropic_request("models", key=key)
    else:
        try:
            with OpenAI(api_key=key, base_url="https://api.openai.com/v1", organization="", project="", timeout=20, max_retries=0) as client:
                client.models.list()
        except APIError as exc:
            raise provider_error(getattr(exc, "status_code", None)) from None
