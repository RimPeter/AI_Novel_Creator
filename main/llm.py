from dataclasses import dataclass

from django.conf import settings
from openai import OpenAI


client = OpenAI(api_key=settings.OPENAI_API_KEY)


@dataclass
class LLMResult:
    text: str
    usage: dict


def call_llm(*, prompt: str, model_name: str, params: dict) -> LLMResult:
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": "You are a professional novelist."},
            {"role": "user", "content": prompt},
        ],
        temperature=params.get("temperature", 0.7),
        max_tokens=params.get("max_tokens", 1500),
    )

    return LLMResult(
        text=response.choices[0].message.content,
        usage={
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        },
    )


def generate_image_data_url(*, prompt: str, model_name: str, size: str = "1024x1024") -> str:
    response = client.images.generate(
        model=model_name,
        prompt=prompt,
        size=size,
        response_format="b64_json",
    )
    data = response.data[0].b64_json
    return f"data:image/png;base64,{data}"
