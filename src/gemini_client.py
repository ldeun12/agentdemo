import json
import os
import time
from typing import Any

from google import genai
from google.genai import types

from config.settings import settings


class GeminiError(RuntimeError):
    """Gemini 호출 또는 응답 처리 오류."""


def get_client() -> genai.Client:
    settings.validate_gemini()
    return genai.Client(api_key=settings.gemini_api_key)


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")

    if start < 0 or end < start:
        raise GeminiError("Gemini 응답에서 JSON 객체를 찾지 못했습니다.")

    try:
        value = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as error:
        raise GeminiError(
            "Gemini가 올바른 JSON을 반환하지 않았습니다."
        ) from error

    if not isinstance(value, dict):
        raise GeminiError("Gemini 응답이 JSON 객체가 아닙니다.")

    return value


def generate_json(
    prompt: str,
    *,
    pdf_bytes: bytes | None = None,
    client: genai.Client | None = None,
) -> dict[str, Any]:
    """PDF와 프롬프트를 전송하고 JSON 결과를 반환합니다."""

    client = client or get_client()
    contents: list[Any] = []

    if pdf_bytes is not None:
        contents.append(
            types.Part.from_bytes(
                data=pdf_bytes,
                mime_type="application/pdf",
            )
        )

    contents.append(prompt)

    configured_fallbacks = os.getenv(
        "GEMINI_FALLBACK_MODELS",
        "gemini-2.5-flash,gemini-2.5-flash-lite",
    ).split(",")

    models = list(
        dict.fromkeys(
            [settings.gemini_model]
            + [
                model.strip()
                for model in configured_fallbacks
                if model.strip()
            ]
        )
    )

    last_error: Exception | None = None

    for model in models:
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0,
                    ),
                )

                return parse_json_object(response.text or "")

            except Exception as error:
                last_error = error
                message = str(error).lower()

                retryable = any(
                    marker in message
                    for marker in (
                        "429",
                        "503",
                        "unavailable",
                        "high demand",
                        "resource_exhausted",
                    )
                )

                if not retryable:
                    raise GeminiError(
                        f"Gemini API 호출에 실패했습니다: {error}"
                    ) from error

                if attempt < 2:
                    time.sleep(2 ** attempt)

        print(
            f"Gemini 모델 혼잡으로 다음 모델을 시도합니다: {model}"
        )

    raise GeminiError(
        "현재 Gemini Flash 모델들이 혼잡합니다. "
        "잠시 후 다시 시도해주세요. "
        f"마지막 오류: {last_error}"
    ) from last_error