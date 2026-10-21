from unittest.mock import patch

import httpx
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import JsonResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from .ai_credentials import (AIConfigurationError, AICredentialMiddleware, ai_user_context,
                             current_ai_request, encrypt_key)
from .ai_providers import ProviderRequestError, anthropic_request, provider_error, request_timeout
from .llm import LLMResult, call_llm
from .models import UserAICredential, UserTextModelPreference, NovelProject, GenerationRun
from .text_models import save_user_fallback, save_user_text_model


@override_settings(AI_CREDENTIAL_ENCRYPTION_KEY=Fernet.generate_key().decode())
class AIFallbackTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="fallback-writer")
        self.other = get_user_model().objects.create_user(username="other-writer")
        for provider in ("openai", "anthropic"):
            UserAICredential.objects.create(user=self.user, provider=provider,
                encrypted_key=encrypt_key("sk-test-secret-key"), key_suffix="-key")
        save_user_text_model(self.user, "gpt-4o-mini")
        save_user_fallback(self.user, enabled=True, model_name="claude-sonnet-4-6")
        self.client.force_login(self.user)

    def call(self):
        with ai_user_context(self.user):
            return call_llm(prompt="Write a story", model_name="gpt-4o-mini", params={"max_tokens": 150})

    @patch("main.llm._call_llm_once")
    def test_primary_success_does_not_call_backup(self, call):
        call.return_value = LLMResult("Primary text", {"total_tokens": 10})
        result = self.call()
        call.assert_called_once()
        self.assertEqual(result.usage["model"], "gpt-4o-mini")
        self.assertNotIn("fallback_used", result.usage)

    @patch("main.llm._call_llm_once")
    def test_transient_failure_uses_backup_once_and_preserves_arguments(self, call):
        for kind in ("connection", "server", "rate_limit"):
            with self.subTest(kind=kind):
                call.reset_mock()
                call.side_effect = [ProviderRequestError("Unavailable", kind=kind), LLMResult("Backup text", {"total_tokens": 20})]
                result = self.call()
                self.assertEqual(call.call_count, 2)
                self.assertEqual(call.call_args.kwargs["model_name"], "claude-sonnet-4-6")
                self.assertEqual(call.call_args.kwargs["prompt"], "Write a story")
                self.assertEqual(call.call_args.kwargs["params"], {"max_tokens": 150})
                self.assertFalse(call.call_args.kwargs["allow_endpoint_retry"])
                self.assertTrue(result.usage["fallback_used"])
                self.assertEqual(result.usage["provider"], "anthropic")
                self.assertEqual(result.usage["fallback_reason"], kind)
                self.assertEqual(request_timeout.get(), 120)

    @patch("main.llm._call_llm_once")
    def test_quota_requires_separate_opt_in(self, call):
        call.side_effect = ProviderRequestError("No credits", kind="quota")
        with self.assertRaisesMessage(ProviderRequestError, "No credits"):
            self.call()
        call.assert_called_once()
        save_user_fallback(self.user, enabled=True, model_name="claude-sonnet-4-6", on_quota=True)
        call.side_effect = [ProviderRequestError("No credits", kind="quota"), LLMResult("Backup", {})]
        self.assertTrue(self.call().usage["fallback_used"])

    @patch("main.llm._call_llm_once")
    def test_configuration_and_invalid_requests_do_not_fallback(self, call):
        for error in (provider_error(401), provider_error(403), provider_error(400), provider_error(404), ValueError("Invalid image")):
            call.reset_mock()
            call.side_effect = error
            with self.assertRaises(ValueError):
                self.call()
            call.assert_called_once()

    @patch("main.llm._call_llm_once")
    def test_both_fail_without_loop_or_raw_error_exposure(self, call):
        call.side_effect = [provider_error(503), AIConfigurationError("secret-raw-error")]
        with self.assertRaisesMessage(AIConfigurationError, "backup could not complete") as error:
            self.call()
        self.assertNotIn("secret-raw-error", str(error.exception))
        self.assertEqual(call.call_count, 2)

    @patch("main.llm._call_llm_once")
    def test_disabled_and_other_user_never_use_backup(self, call):
        save_user_fallback(self.user, enabled=False)
        for user in (self.user, self.other):
            call.reset_mock()
            call.side_effect = provider_error(503)
            with ai_user_context(user), self.assertRaises(ProviderRequestError):
                call_llm(prompt="Write", model_name="gpt-4o-mini", params={})
            call.assert_called_once()

    def test_settings_reject_same_provider_or_missing_connection(self):
        with self.assertRaisesMessage(ValueError, "different provider"):
            save_user_fallback(self.user, enabled=True, model_name="gpt-4.1")
        UserAICredential.objects.filter(user=self.user, provider="anthropic").delete()
        with self.assertRaisesMessage(AIConfigurationError, "Connect your Claude"):
            save_user_fallback(self.user, enabled=True, model_name="claude-sonnet-4-6")

    def test_settings_save_render_remove_and_change_primary(self):
        response = self.client.get(reverse("ai-settings"))
        self.assertContains(response, "Backup writing provider")
        response = self.client.post(reverse("ai-settings"), {"action": "fallback", "fallback_enabled": "on",
            "fallback_model": "claude-sonnet-4-6", "fallback_on_quota": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(UserTextModelPreference.objects.get(user=self.user).fallback_on_quota)
        save_user_text_model(self.user, "claude-sonnet-4-6")
        self.assertFalse(UserTextModelPreference.objects.get(user=self.user).fallback_enabled)
        save_user_text_model(self.user, "gpt-4o-mini")
        save_user_fallback(self.user, enabled=True, model_name="claude-sonnet-4-6")
        self.client.post(reverse("ai-settings"), {"action": "remove", "provider": "anthropic"})
        self.assertFalse(UserTextModelPreference.objects.get(user=self.user).fallback_enabled)

    @patch("main.llm._call_llm_once")
    def test_missing_backup_key_does_not_borrow_another_users_key(self, call):
        UserAICredential.objects.filter(user=self.user, provider="anthropic").update(user=self.other)
        call.side_effect = provider_error(503)
        with self.assertRaisesMessage(AIConfigurationError, "backup could not complete"):
            self.call()
        call.assert_called_once()

    @patch("main.llm._call_llm_once")
    def test_notifications_for_json_and_html_and_context_reset(self, call):
        for accept in ("application/json", "text/html"):
            call.side_effect = [provider_error(503), LLMResult("Backup", {})]
            request = RequestFactory().post("/generate/", HTTP_ACCEPT=accept)
            request.user = self.user
            request.session = {}
            request._messages = FallbackStorage(request)
            def view(request):
                call_llm(prompt="Write", model_name="gpt-4o-mini", params={})
                return JsonResponse({"ok": True})
            response = AICredentialMiddleware(view)(request)
            self.assertIn("claude-sonnet-4-6", response["X-AI-Fallback-Notices"])
            self.assertEqual(len(list(request._messages)), 0 if accept == "application/json" else 1)
            self.assertIsNone(current_ai_request.get())

    @patch("main.llm._call_llm_once")
    def test_generation_history_records_actual_model_once(self, call):
        from .views import _call_tracked_llm
        project = NovelProject.objects.create(title="Fallback story", owner=self.user)
        call.side_effect = [provider_error(503), LLMResult("Backup story", {"total_tokens": 20})]
        with ai_user_context(self.user):
            _call_tracked_llm(project=project, action_label="Test", prompt="Write", model_name="gpt-4o-mini", params={})
        run = GenerationRun.objects.get(project=project)
        self.assertEqual(run.model_name, "claude-sonnet-4-6")
        self.assertEqual(run.output_text, "Backup story")
        self.assertTrue(run.usage["fallback_used"])

    @patch("main.ai_providers.httpx.Client")
    def test_anthropic_timeout_is_eligible(self, client):
        client.return_value.__enter__.return_value.request.side_effect = httpx.ReadTimeout("secret")
        with ai_user_context(self.user), self.assertRaises(ProviderRequestError) as error:
            anthropic_request("messages", payload={})
        self.assertEqual(error.exception.kind, "connection")

    def test_quota_classification(self):
        self.assertEqual(provider_error(429, "insufficient_quota").kind, "quota")
        self.assertEqual(provider_error(400, "credit_balance_too_low").kind, "quota")
        self.assertEqual(provider_error(429).kind, "rate_limit")

    @patch("main.llm._call_llm_once")
    def test_reverse_fallback_and_refusal_does_not_switch(self, call):
        save_user_text_model(self.user, "claude-sonnet-4-6")
        save_user_fallback(self.user, enabled=True, model_name="gpt-4o-mini")
        call.side_effect = [provider_error(529), LLMResult("OpenAI text", {})]
        with ai_user_context(self.user):
            result = call_llm(prompt="Write", model_name="claude-sonnet-4-6", params={})
        self.assertEqual(result.usage["provider"], "openai")
        call.reset_mock()
        call.side_effect = None
        call.return_value = LLMResult("", {}, finish_reason="refusal")
        with ai_user_context(self.user):
            result = call_llm(prompt="Write", model_name="claude-sonnet-4-6", params={})
        call.assert_called_once()
        self.assertEqual(result.finish_reason, "refusal")

    @patch("main.ai_providers.OpenAI")
    def test_openai_transport_timeout_classification_and_timeout_limit(self, client):
        from openai import APITimeoutError
        from .ai_providers import openai_request
        client.return_value.__enter__.return_value.responses.create.side_effect = APITimeoutError(request=httpx.Request("POST", "https://example.com"))
        with ai_user_context(self.user), self.assertRaises(ProviderRequestError) as error:
            openai_request("responses.create")
        self.assertEqual(error.exception.kind, "connection")
        self.assertEqual(client.call_args.kwargs["max_retries"], 0)

    @patch("main.llm._call_llm_once")
    def test_each_attempt_has_bounded_timeout(self, call):
        timeouts = []
        def respond(**kwargs):
            timeouts.append(request_timeout.get())
            if len(timeouts) == 1:
                raise provider_error(503)
            return LLMResult("Backup", {})
        call.side_effect = respond
        self.call()
        self.assertEqual(timeouts, [60, 60])

    @patch("main.llm._call_llm_once")
    def test_failure_does_not_create_successful_history(self, call):
        from .views import _call_tracked_llm
        project = NovelProject.objects.create(title="Existing story", owner=self.user, seed_idea="Keep this")
        call.side_effect = provider_error(503)
        with ai_user_context(self.user), self.assertRaises(AIConfigurationError):
            _call_tracked_llm(project=project, action_label="Test", prompt="Write", model_name="gpt-4o-mini", params={})
        self.assertFalse(GenerationRun.objects.filter(project=project).exists())
        project.refresh_from_db()
        self.assertEqual(project.seed_idea, "Keep this")
