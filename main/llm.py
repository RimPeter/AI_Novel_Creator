from dataclasses import dataclass

from django.conf import settings
from openai import OpenAI


client = OpenAI(api_key=settings.OPENAI_API_KEY)
SYSTEM_PROMPT = "You are a professional novelist. Never use the em dash character (U+2014); use a plain hyphen instead."
IMAGE_MODEL_ALIASES = {
    "gpt-image-2": "gpt-image-1",
}


@dataclass
class LLMResult:
    text: str
    usage: dict
    finish_reason: str = ""


def _normalize_llm_text(text: str | None) -> str:
    return (text or "").replace("\u2014", "-")


def _uses_responses_api(model_name: str) -> bool:
    normalized = (model_name or "").strip().lower()
    return normalized.startswith("gpt-5") or normalized.startswith("o")


def _model_supports_custom_temperature(model_name: str) -> bool:
    normalized = (model_name or "").strip().lower()
    if normalized.startswith("gpt-5") or normalized.startswith("o"):
        return False
    return True


def _get_usage_value(usage, key: str) -> int:
    if usage is None:
        return 0
    value = getattr(usage, key, 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _coerce_text_fragment(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _normalize_llm_text(value)
    nested_value = getattr(value, "value", None)
    if isinstance(nested_value, str):
        return _normalize_llm_text(nested_value)
    return ""


def _get_object_value(value, key: str, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _normalize_finish_reason(value) -> str:
    return str(value or "").strip().lower()


def _responses_reasoning_effort(model_name: str) -> str | None:
    normalized = (model_name or "").strip().lower()
    if normalized.startswith("gpt-5") or normalized.startswith("o"):
        return "low"
    return None


def normalize_image_model_name(model_name: str) -> str:
    normalized = (model_name or "").strip()
    if not normalized:
        return "gpt-image-1"
    return IMAGE_MODEL_ALIASES.get(normalized.lower(), normalized)


def _iter_nested_text_fragments(value, *, depth: int = 0):
    if depth > 6 or value is None:
        return
    if isinstance(value, str):
        text = _normalize_llm_text(value)
        if text.strip():
            yield text
        return
    if isinstance(value, dict):
        preferred_keys = ("output_text", "output", "content", "text", "value")
        seen = set()
        for key in preferred_keys:
            if key in value:
                seen.add(key)
                yield from _iter_nested_text_fragments(value.get(key), depth=depth + 1)
        for key, nested in value.items():
            if key in seen or key in {"type", "role", "status", "id"}:
                continue
            yield from _iter_nested_text_fragments(nested, depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_nested_text_fragments(item, depth=depth + 1)
        return
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()
        except Exception:
            dumped = None
        if dumped is not None:
            yield from _iter_nested_text_fragments(dumped, depth=depth + 1)
            return
    for attr in ("output_text", "output", "content", "text", "value"):
        if hasattr(value, attr):
            yield from _iter_nested_text_fragments(getattr(value, attr), depth=depth + 1)


def _extract_responses_text(response) -> str:
    output_text = _coerce_text_fragment(_get_object_value(response, "output_text", None))
    if output_text.strip():
        return output_text

    for item in _get_object_value(response, "output", []) or []:
        for content in _get_object_value(item, "content", []) or []:
            text_value = _coerce_text_fragment(_get_object_value(content, "text", None))
            if text_value.strip():
                return text_value
            for candidate in _iter_nested_text_fragments(content):
                if candidate.strip():
                    return candidate

    return ""


def _call_chat_completions_llm(*, prompt: str, model_name: str, params: dict) -> LLMResult:
    max_completion_tokens = params.get("max_completion_tokens", params.get("max_tokens", 1500))
    request_kwargs = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_completion_tokens": max_completion_tokens,
    }
    if _model_supports_custom_temperature(model_name):
        request_kwargs["temperature"] = params.get("temperature", 0.7)

    response = client.chat.completions.create(**request_kwargs)

    return LLMResult(
        text=_normalize_llm_text(response.choices[0].message.content),
        usage={
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        },
        finish_reason=_normalize_finish_reason(_get_object_value(response.choices[0], "finish_reason", "")),
    )


def call_llm(*, prompt: str, model_name: str, params: dict) -> LLMResult:
    max_output_tokens = params.get("max_completion_tokens", params.get("max_tokens", 1500))

    if _uses_responses_api(model_name):
        request_kwargs = {
            "model": model_name,
            "instructions": SYSTEM_PROMPT,
            "input": prompt,
            "max_output_tokens": max_output_tokens,
        }
        reasoning_effort = _responses_reasoning_effort(model_name)
        if reasoning_effort:
            request_kwargs["reasoning"] = {"effort": reasoning_effort}
        response = client.responses.create(**request_kwargs)
        prompt_tokens = _get_usage_value(getattr(response, "usage", None), "input_tokens")
        completion_tokens = _get_usage_value(getattr(response, "usage", None), "output_tokens")
        total_tokens = _get_usage_value(getattr(response, "usage", None), "total_tokens")
        if not total_tokens:
            total_tokens = prompt_tokens + completion_tokens
        text = _extract_responses_text(response)
        finish_reason = _normalize_finish_reason(
            _get_object_value(_get_object_value(response, "incomplete_details", None), "reason", "")
            or ("length" if _normalize_finish_reason(_get_object_value(response, "status", "")) == "incomplete" else "")
        )
        if text.strip():
            return LLMResult(
                text=text,
                usage={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                },
                finish_reason=finish_reason,
            )

        fallback_result = _call_chat_completions_llm(prompt=prompt, model_name=model_name, params=params)
        fallback_usage = fallback_result.usage or {}
        combined_prompt_tokens = prompt_tokens + int(fallback_usage.get("prompt_tokens", 0) or 0)
        combined_completion_tokens = completion_tokens + int(fallback_usage.get("completion_tokens", 0) or 0)
        combined_total_tokens = total_tokens + int(fallback_usage.get("total_tokens", 0) or 0)
        if not combined_total_tokens:
            combined_total_tokens = combined_prompt_tokens + combined_completion_tokens
        return LLMResult(
            text=fallback_result.text,
            usage={
                "prompt_tokens": combined_prompt_tokens,
                "completion_tokens": combined_completion_tokens,
                "total_tokens": combined_total_tokens,
            },
            finish_reason=fallback_result.finish_reason,
        )

    return _call_chat_completions_llm(prompt=prompt, model_name=model_name, params=params)


def generate_image_data_url(*, prompt: str, model_name: str, size: str = "1024x1024") -> str:
    resolved_model_name = normalize_image_model_name(model_name)
    response = client.images.generate(
        model=resolved_model_name,
        prompt=prompt,
        size=size,
        response_format="b64_json",
    )
    data = response.data[0].b64_json
    return f"data:image/png;base64,{data}"
