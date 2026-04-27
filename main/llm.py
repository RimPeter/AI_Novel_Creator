import base64
from dataclasses import dataclass
from io import BytesIO

from django.conf import settings
from openai import OpenAI
from PIL import Image, ImageFilter, ImageStat


client = OpenAI(api_key=settings.OPENAI_API_KEY)
SYSTEM_PROMPT = "You are a professional novelist. Never use the em dash character (U+2014); use a plain hyphen instead."


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
    return "gpt-image-2"


def _is_gpt_image_model(model_name: str) -> bool:
    return (model_name or "").strip().lower().startswith("gpt-image-")


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


def _chat_user_content(prompt: str, image_data_url: str = ""):
    if not image_data_url:
        return prompt
    return [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": image_data_url}},
    ]


def _responses_input(prompt: str, image_data_url: str = ""):
    if not image_data_url:
        return prompt
    return [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": image_data_url},
            ],
        }
    ]


def _call_chat_completions_llm(*, prompt: str, model_name: str, params: dict, image_data_url: str = "") -> LLMResult:
    max_completion_tokens = params.get("max_completion_tokens", params.get("max_tokens", 1500))
    request_kwargs = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _chat_user_content(prompt, image_data_url)},
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


def call_llm(*, prompt: str, model_name: str, params: dict, image_data_url: str = "") -> LLMResult:
    max_output_tokens = params.get("max_completion_tokens", params.get("max_tokens", 1500))
    image_data_url = (image_data_url or "").strip()

    if _uses_responses_api(model_name):
        request_kwargs = {
            "model": model_name,
            "instructions": SYSTEM_PROMPT,
            "input": _responses_input(prompt, image_data_url),
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

        fallback_result = _call_chat_completions_llm(prompt=prompt, model_name=model_name, params=params, image_data_url=image_data_url)
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

    return _call_chat_completions_llm(prompt=prompt, model_name=model_name, params=params, image_data_url=image_data_url)


def generate_image_data_url(*, prompt: str, model_name: str, size: str = "1024x1024") -> str:
    resolved_model_name = normalize_image_model_name(model_name)
    image_params = {
        "model": resolved_model_name,
        "prompt": prompt,
        "size": size,
    }
    if _is_gpt_image_model(resolved_model_name):
        image_params["output_format"] = "png"
    else:
        image_params["response_format"] = "b64_json"

    response = client.images.generate(**image_params)
    data = response.data[0].b64_json
    return f"data:image/png;base64,{data}"


def _data_url_to_image(data_url: str) -> Image.Image:
    encoded = data_url.split(",", 1)[1] if "," in data_url else data_url
    return Image.open(BytesIO(base64.b64decode(encoded))).convert("RGBA")


def _image_to_data_url(image: Image.Image) -> str:
    output = BytesIO()
    image.save(output, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(output.getvalue()).decode('ascii')}"


def _match_image_tone_to_reference(*, edited_data_url: str, reference_data_url: str) -> str:
    reference = _data_url_to_image(reference_data_url)
    edited = _data_url_to_image(edited_data_url)
    if reference.size != edited.size:
        reference = reference.resize(edited.size, Image.Resampling.LANCZOS)

    reference_rgb = reference.convert("RGB")
    edited_rgb = edited.convert("RGB")
    reference_stat = ImageStat.Stat(reference_rgb)
    edited_stat = ImageStat.Stat(edited_rgb)

    lut = []
    for channel in range(3):
        reference_mean = reference_stat.mean[channel]
        edited_mean = edited_stat.mean[channel]
        reference_std = max(reference_stat.stddev[channel], 1.0)
        edited_std = max(edited_stat.stddev[channel], 1.0)
        scale = max(0.75, min(1.35, reference_std / edited_std))
        lut.extend(
            max(0, min(255, int(round((value - edited_mean) * scale + reference_mean))))
            for value in range(256)
        )

    matched = edited_rgb.point(lut)
    # A tiny median blend suppresses global speckle/static without visibly blurring linework.
    denoised = matched.filter(ImageFilter.MedianFilter(size=3))
    matched = Image.blend(matched, denoised, 0.10)
    matched.putalpha(edited.getchannel("A"))
    return _image_to_data_url(matched)


def edit_image_data_url(*, prompt: str, image_data_url: str, model_name: str, size: str = "1024x1024") -> str:
    resolved_model_name = normalize_image_model_name(model_name)
    if "," in image_data_url:
        _header, encoded = image_data_url.split(",", 1)
    else:
        encoded = image_data_url
    image_file = BytesIO(base64.b64decode(encoded))
    image_file.name = "reference.png"
    image_params = {
        "model": resolved_model_name,
        "image": image_file,
        "prompt": prompt,
        "size": size,
    }
    if _is_gpt_image_model(resolved_model_name):
        image_params["output_format"] = "png"
        image_params["quality"] = "high"
    else:
        image_params["response_format"] = "b64_json"

    response = client.images.edit(**image_params)
    data = response.data[0].b64_json
    edited_data_url = f"data:image/png;base64,{data}"
    return _match_image_tone_to_reference(edited_data_url=edited_data_url, reference_data_url=image_data_url)
