from django.conf import settings

from .models import UserTextModelPreference


def model_provider(model_name):
    return "anthropic" if model_name.startswith("claude-") else "openai"


def save_user_fallback(user, *, enabled, model_name="", on_quota=False):
    from .ai_credentials import get_api_key
    if enabled:
        if model_name not in {item["value"] for item in get_available_text_models()}:
            raise ValueError("Choose a valid backup model.")
        if model_provider(model_name) == model_provider(get_user_text_model(user)):
            raise ValueError("Choose a backup model from a different provider.")
        get_api_key(model_provider(get_user_text_model(user)), user)
        get_api_key(model_provider(model_name), user)
    UserTextModelPreference.objects.update_or_create(user=user, defaults={
        "fallback_enabled": enabled, "fallback_model_name": model_name if enabled else "",
        "fallback_on_quota": bool(enabled and on_quota),
    })


TEXT_MODEL_OPTIONS = [
    # Current general-purpose text models, verified against provider catalogs.
    ("gpt-6.1-sol", "GPT-6.1 Sol"),
    ("gpt-6-astra", "GPT-6 Astra"),
    ("gpt-6-luna", "GPT-6 Luna"),
    ("claude-sonnet-5-5", "Claude Sonnet 5.5"),
    ("claude-opus-5-5", "Claude Opus 5.5"),
    ("claude-haiku-5-5", "Claude Haiku 5.5"),
    ("claude-fable-5-1", "Claude Fable 5.1"),
    # Retain existing choices for users who already selected them.
    ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
    ("claude-opus-4-6", "Claude Opus 4.6"),
    ("claude-haiku-4-5", "Claude Haiku 4.5"),
    ("gpt-4o-mini", "GPT-4o mini"),
    ("gpt-4.1-mini", "GPT-4.1 mini"),
    ("gpt-4.1", "GPT-4.1"),
    ("gpt-5-mini", "GPT-5 mini"),
    ("gpt-5", "GPT-5"),
    ("o4-mini", "o4-mini"),
    ("o3", "o3"),
]


def get_default_text_model() -> str:
    return (getattr(settings, "OPENAI_MODEL", "gpt-4o-mini") or "gpt-4o-mini").strip()


def get_available_text_models() -> list[dict]:
    default_model = get_default_text_model()
    options = []
    seen = set()
    for value, label in TEXT_MODEL_OPTIONS:
        option_value = (value or "").strip()
        if not option_value or option_value in seen:
            continue
        seen.add(option_value)
        option_label = f"{label} (default)" if option_value == default_model else label
        options.append({"value": option_value, "label": option_label})

    if default_model and default_model not in seen:
        options.insert(0, {"value": default_model, "label": f"{default_model} (default)"})

    return options


def get_user_text_model(user) -> str:
    default_model = get_default_text_model()
    if not getattr(user, "is_authenticated", False):
        return default_model

    preference = UserTextModelPreference.objects.filter(user=user).first()
    selected_model = (getattr(preference, "text_model_name", "") or "").strip()
    return selected_model or default_model


def save_user_text_model(user, model_name: str) -> str:
    selected_model = (model_name or "").strip()
    available_models = {option["value"] for option in get_available_text_models()}
    if selected_model not in available_models:
        raise ValueError("Choose a valid model.")

    from .ai_credentials import get_api_key
    get_api_key("anthropic" if selected_model.startswith("claude-") else "openai", user)

    default_model = get_default_text_model()
    preference, _created = UserTextModelPreference.objects.get_or_create(user=user)
    stored_value = "" if selected_model == default_model else selected_model
    if preference.text_model_name != stored_value:
        preference.text_model_name = stored_value
        if preference.fallback_enabled and model_provider(preference.fallback_model_name) == model_provider(selected_model):
            preference.fallback_enabled = False
        preference.save(update_fields=["text_model_name", "fallback_enabled", "updated_at"])
    return selected_model
