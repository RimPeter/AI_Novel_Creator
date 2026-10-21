from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_http_methods

from .ai_credentials import AIConfigurationError, cipher, encrypt_key, get_api_key
from .ai_providers import validate_key
from .models import UserAICredential
from .text_models import get_available_text_models, get_user_text_model, save_user_text_model


class ConnectionForm(forms.Form):
    provider = forms.ChoiceField(choices=[("openai", "OpenAI (GPT)"), ("anthropic", "Anthropic (Claude)")])
    api_key = forms.CharField(min_length=12, max_length=512, strip=True, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))


@login_required
@never_cache
@sensitive_post_parameters("api_key")
@sensitive_variables()
@require_http_methods(["GET", "POST"])
def ai_settings(request):
    form = ConnectionForm()
    if request.method == "POST":
        action = request.POST.get("action")
        provider = request.POST.get("provider")
        try:
            if action == "connect":
                form = ConnectionForm(request.POST)
                if form.is_valid():
                    cipher()  # Fail before sending a key if secure storage is unavailable.
                    provider = form.cleaned_data["provider"]
                    key = form.cleaned_data["api_key"]
                    validate_key(provider, key)
                    first_connection = not UserAICredential.objects.filter(user=request.user).exists()
                    UserAICredential.objects.update_or_create(user=request.user, provider=provider, defaults={"encrypted_key": encrypt_key(key), "key_suffix": key[-4:]})
                    if first_connection:
                        save_user_text_model(request.user, "claude-sonnet-4-6" if provider == "anthropic" else "gpt-4o-mini")
                    messages.success(request, "API key verified and saved. Model availability and spending limits are managed by your provider.")
                    return redirect("ai-settings")
            elif action in ("remove", "test") and provider in ("openai", "anthropic"):
                if action == "remove":
                    UserAICredential.objects.filter(user=request.user, provider=provider).delete()
                    messages.success(request, "API key removed from this app.")
                else:
                    validate_key(provider, get_api_key(provider, request.user))
                    messages.success(request, "Connection verified.")
                return redirect("ai-settings")
            elif action == "model":
                model = request.POST.get("model", "")
                provider = "anthropic" if model.startswith("claude-") else "openai"
                get_api_key(provider, request.user)
                save_user_text_model(request.user, model)
                messages.success(request, "Writing model saved.")
                return redirect("ai-settings")
            else:
                messages.error(request, "Choose a valid action.")
        except (AIConfigurationError, ValueError) as exc:
            messages.error(request, str(exc))
    return render(request, "main/ai_settings.html", {
        "form": form,
        "connections": UserAICredential.objects.filter(user=request.user).only("provider", "key_suffix"),
        "models": get_available_text_models(),
        "selected_model": get_user_text_model(request.user),
    })


@login_required
@require_http_methods(["GET", "POST"])
def retired_checkout(request):
    messages.info(request, "Plans have been replaced by your own API key. Connect a provider to get started.")
    return redirect("ai-settings")
