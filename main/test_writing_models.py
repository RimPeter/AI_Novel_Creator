from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .llm import call_llm


class WritingModelCompatibilityTests(SimpleTestCase):
    @patch("main.llm.openai_request")
    def test_current_gpt_models_use_responses_without_temperature(self, request):
        request.return_value = SimpleNamespace(output_text="A new chapter.", usage=None)
        for model in ("gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna"):
            with self.subTest(model=model):
                result = call_llm(prompt="Write a chapter", model_name=model, params={"temperature": 0.7})
                self.assertEqual(result.text, "A new chapter.")
                self.assertEqual(request.call_args.args, ("responses.create",))
                self.assertEqual(request.call_args.kwargs["reasoning"], {"effort": "low"})
                self.assertNotIn("temperature", request.call_args.kwargs)

    @patch("main.llm.openai_request")
    def test_gpt6_truncation_does_not_retry_via_chat(self, request):
        request.return_value = SimpleNamespace(output_text="", output=[], usage=None, status="incomplete", incomplete_details={"reason": "max_output_tokens"})
        result = call_llm(prompt="Write", model_name="gpt-6.1-sol", params={})
        self.assertEqual(result.finish_reason, "max_output_tokens")
        request.assert_called_once()

    @patch("main.llm.anthropic_request")
    def test_current_claude_models_extract_text_not_thinking(self, request):
        request.return_value = {"content": [{"type": "thinking", "thinking": "internal"}, {"type": "text", "text": "A new chapter."}], "usage": {}, "stop_reason": "end_turn"}
        for model in ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5", "claude-fable-5-1"):
            with self.subTest(model=model):
                result = call_llm(prompt="Write", model_name=model, params={})
                self.assertEqual(result.text, "A new chapter.")
                self.assertEqual(request.call_args.kwargs["payload"]["model"], model)
