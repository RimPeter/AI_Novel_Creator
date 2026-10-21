from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from .ai_credentials import AIConfigurationError, ai_user_context, current_ai_user, encrypt_key, get_api_key
from .ai_providers import openai_request
from .llm import call_llm, generate_image_data_url
from .models import UserAICredential


@override_settings(AI_CREDENTIAL_ENCRYPTION_KEY=Fernet.generate_key().decode())
class AISettingsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="writer", password="test-pass")
        self.other = get_user_model().objects.create_user(username="other")
        self.client.force_login(self.user)

    def connect(self, user=None, provider="openai", key="sk-private-test-1234"):
        return UserAICredential.objects.create(user=user or self.user, provider=provider, encrypted_key=encrypt_key(key), key_suffix=key[-4:])

    @patch("main.ai_settings.validate_key")
    def test_connect_encrypts_and_masks_key(self, validate):
        response = self.client.post(reverse("ai-settings"), {"action": "connect", "provider": "openai", "api_key": "sk-private-test-1234"}, follow=True)
        self.assertContains(response, "1234")
        self.assertNotContains(response, "sk-private-test")
        record = UserAICredential.objects.get(user=self.user)
        self.assertNotIn("sk-private", record.encrypted_key)
        self.assertEqual(get_api_key("openai", self.user), "sk-private-test-1234")
        self.assertIn("no-store", response["Cache-Control"])

    @patch("main.ai_settings.validate_key", side_effect=AIConfigurationError("Invalid credentials"))
    def test_failed_replacement_preserves_key(self, validate):
        self.connect()
        response = self.client.post(reverse("ai-settings"), {"action": "connect", "provider": "openai", "api_key": "bad-secret-value"})
        self.assertContains(response, "Invalid credentials")
        self.assertNotContains(response, "bad-secret-value")
        self.assertEqual(get_api_key("openai", self.user), "sk-private-test-1234")

    def test_remove_only_affects_current_user(self):
        self.connect()
        self.connect(self.other)
        self.client.post(reverse("ai-settings"), {"action": "remove", "provider": "openai", "user_id": self.other.pk})
        self.assertFalse(UserAICredential.objects.filter(user=self.user).exists())
        self.assertTrue(UserAICredential.objects.filter(user=self.other).exists())
        with self.assertRaises(AIConfigurationError):
            get_api_key("openai", self.user)

    @override_settings(OPENAI_API_KEY="deployment-key-must-not-be-used")
    @patch("main.ai_providers.OpenAI")
    def test_missing_key_never_uses_server_key(self, client):
        with ai_user_context(self.user), self.assertRaises(AIConfigurationError):
            openai_request("models.list")
        client.assert_not_called()

    def test_context_is_reset_even_after_error(self):
        with self.assertRaises(RuntimeError), ai_user_context(self.user):
            raise RuntimeError()
        self.assertIsNone(current_ai_user.get())

    def test_parallel_contexts_are_isolated(self):
        def read_context(user):
            with ai_user_context(user):
                return current_ai_user.get().pk
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(read_context, [self.user, self.other])), [self.user.pk, self.other.pk])

    @patch("main.ai_providers.OpenAI")
    def test_transport_uses_current_users_key(self, factory):
        self.connect()
        self.connect(self.other, key="sk-other-user-5678")
        for user, expected in [(self.user, "sk-private-test-1234"), (self.other, "sk-other-user-5678")]:
            with ai_user_context(user):
                openai_request("models.list")
            self.assertEqual(factory.call_args.kwargs["api_key"], expected)

    @patch("main.ai_settings.validate_key")
    @override_settings(AI_CREDENTIAL_ENCRYPTION_KEY="")
    def test_missing_encryption_configuration_fails_closed(self, validate):
        response = self.client.post(reverse("ai-settings"), {"action": "connect", "provider": "openai", "api_key": "sk-test-secret"})
        self.assertContains(response, "not configured")
        self.assertFalse(UserAICredential.objects.exists())
        validate.assert_not_called()

    def test_wrong_encryption_key_has_safe_error(self):
        self.connect()
        with override_settings(AI_CREDENTIAL_ENCRYPTION_KEY=Fernet.generate_key().decode()), self.assertRaisesMessage(AIConfigurationError, "Reconnect"):
            get_api_key("openai", self.user)

    def test_model_requires_matching_connection(self):
        self.connect()
        response = self.client.post(reverse("ai-settings"), {"action": "model", "model": "claude-sonnet-4-6"})
        self.assertContains(response, "Connect your Claude")

    @patch("main.ai_settings.validate_key")
    def test_first_claude_connection_selects_claude(self, validate):
        from .text_models import get_user_text_model
        self.client.post(reverse("ai-settings"), {"action": "connect", "provider": "anthropic", "api_key": "sk-ant-test-secret"})
        self.assertEqual(get_user_text_model(self.user), "claude-sonnet-4-6")

    def test_retired_checkout_never_creates_stripe_session(self):
        with patch("main.views.create_checkout_session") as checkout:
            self.assertRedirects(self.client.post(reverse("billing-checkout"), {"plan": "monthly"}), reverse("ai-settings"))
        checkout.assert_not_called()
        self.assertRedirects(self.client.get(reverse("billing")), reverse("ai-settings"))
        response = self.client.get(reverse("billing-history"))
        self.assertContains(response, "Invoices")
        self.assertNotContains(response, "billing-plan-card")

    def test_settings_require_login_and_csrf(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get(reverse("ai-settings")).status_code, 302)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse("ai-settings"), {"action": "remove", "provider": "openai"}).status_code, 403)

    @patch("main.llm.anthropic_request")
    def test_claude_normalizes_usage_images_and_truncation(self, request):
        request.return_value = {"content": [{"type": "text", "text": "Hello"}], "stop_reason": "max_tokens", "usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 5}}
        result = call_llm(prompt="Write", model_name="claude-sonnet-4-6", params={"max_tokens": 100}, image_data_url="data:image/png;base64,aGVsbG8=")
        self.assertEqual(result.text, "Hello")
        self.assertEqual(result.usage["total_tokens"], 35)
        self.assertEqual(result.finish_reason, "length")
        self.assertEqual(request.call_args.kwargs["payload"]["messages"][0]["content"][0]["type"], "image")

    @patch("main.ai_providers.OpenAI")
    def test_claude_only_account_cannot_generate_images(self, client):
        self.connect(provider="anthropic")
        with ai_user_context(self.user), self.assertRaisesMessage(AIConfigurationError, "OpenAI"):
            generate_image_data_url(prompt="Draw", model_name="gpt-image-2")
        client.assert_not_called()

    @patch("main.ai_providers.httpx.Client")
    def test_anthropic_quota_error_is_sanitized(self, factory):
        self.connect(provider="anthropic")
        response = factory.return_value.__enter__.return_value.request.return_value
        response.status_code = 429
        with ai_user_context(self.user), self.assertRaisesMessage(AIConfigurationError, "quota"):
            call_llm(prompt="Write", model_name="claude-sonnet-4-6", params={})
        response.json.assert_not_called()
