"""Provider transport, with bounded requests and safe public errors."""
import httpx
from openai import OpenAI, APIError
from django.views.decorators.debug import sensitive_variables

from .ai_credentials import AIConfigurationError, get_api_key


def provider_error(status):
    if status in (401, 403):
        return AIConfigurationError("The provider rejected your API key or permissions. Check AI Settings.")
    if status in (402, 429):
        return AIConfigurationError("Your provider quota or rate limit was reached. Check your provider account or try again later.")
    if status == 404:
        return AIConfigurationError("This model is unavailable for your API key. Choose another model in AI Settings.")
    return AIConfigurationError("The AI provider could not complete the request. Check your model settings and try again.")


@sensitive_variables()
def openai_request(resource, **kwargs):
    key = get_api_key("openai")
    try:
        with OpenAI(api_key=key, base_url="https://api.openai.com/v1", organization="", project="", timeout=120, max_retries=0) as client:
            method = client
            for part in resource.split("."):
                method = getattr(method, part)
            return method(**kwargs)
    except APIError as exc:
        raise provider_error(getattr(exc, "status_code", None)) from None


@sensitive_variables()
def anthropic_request(path, *, key=None, payload=None):
    key = key or get_api_key("anthropic")
    try:
        with httpx.Client(timeout=120) as client:
            response = client.request(
                "POST" if payload is not None else "GET",
                "https://api.anthropic.com/v1/" + path,
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                **({"json": payload} if payload is not None else {}),
            )
            if response.status_code >= 400:
                raise provider_error(response.status_code)
            return response.json()
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
