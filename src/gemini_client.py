import json
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
        raise GeminiError("Gemini가 올바른 JSON을 반환하지 않았습니다.") from error
    if not isinstance(value, dict):
        raise GeminiError("Gemini 응답이 JSON 객체가 아닙니다.")
    return value


def generate_json(
    prompt: str,
    *,
    pdf_bytes: bytes | None = None,
    client: genai.Client | None = None,
) -> dict[str, Any]:
    """텍스트와 선택적 PDF를 Gemini에 인라인 전송하고 JSON을 반환합니다."""

    client = client or get_client()
    contents: list[Any] = []
    if pdf_bytes is not None:
        contents.append(types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"))
    contents.append(prompt)

    try:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
            ),
        )
    except Exception as error:
        raise GeminiError(f"Gemini API 호출에 실패했습니다: {error}") from error

    return parse_json_object(response.text or "")
