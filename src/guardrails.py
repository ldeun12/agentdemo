"""
app.py가 기대하는 두 함수를 제공합니다.
이 파일은 그동안 비어 있었기 때문에 (0바이트) 임포트 자체가
실패하고 있었습니다.
"""

from typing import Any

MAX_REQUEST_LENGTH = 1000

# 본인 성적증명서 기반 학업 상담이라는 목적을 벗어나는 요청을 막기 위한
# 간단한 키워드 기반 차단 목록입니다. 완벽한 방어는 아니지만, 명백히
# 벗어난 요청(다른 사람 정보 요청, 프롬프트 조작 시도 등)을 걸러냅니다.
_BLOCKED_PATTERNS = (
    "다른 학생",
    "타인의 성적",
    "친구 성적",
    "룸메이트 성적",
    "시스템 프롬프트",
    "system prompt",
    "ignore previous",
    "ignore all previous",
    "지시를 무시",
    "역할을 무시",
    "역할극",
    "너는 이제",
    "jailbreak",
)


def validate_user_request(user_request: str | None) -> dict[str, Any]:
    """
    사용자가 입력한 '추가 요청' 텍스트가 안전/적절한지 검사합니다.

    반환값: {"allowed": bool, "message": str}
    allowed가 False면 message에 사용자에게 보여줄 안내 문구가 담깁니다.
    """

    if not user_request or not user_request.strip():
        return {"allowed": True, "message": ""}

    text = user_request.strip()

    if len(text) > MAX_REQUEST_LENGTH:
        return {
            "allowed": False,
            "message": (
                f"요청이 너무 깁니다. {MAX_REQUEST_LENGTH}자 이내로 "
                "입력해주세요."
            ),
        }

    lowered = text.lower()

    for pattern in _BLOCKED_PATTERNS:
        if pattern.lower() in lowered:
            return {
                "allowed": False,
                "message": (
                    "본인 성적증명서를 기준으로 한 학업 상담 목적에 맞지 "
                    "않는 요청은 처리할 수 없습니다. 졸업 요건, 추천 과목, "
                    "장학금 전략 등 성적과 관련된 질문을 입력해주세요."
                ),
            }

    return {"allowed": True, "message": ""}


def _strip_pii_keys(data: dict[str, Any]) -> None:
    """dict에서 개인식별정보에 해당하는 키를 제거합니다 (in-place)."""

    for key in ("student_name", "student_number"):
        data.pop(key, None)


def sanitize_strategy_report(strategy_report: Any) -> dict[str, Any]:
    """
    StrategyReport(pydantic 모델 또는 dict)를 화면에 표시하기 안전한
    dict로 변환합니다. TranscriptData 스키마 자체에서 이미
    student_name/student_number를 제외(exclude=True)하고 있지만,
    이중 방어로 한 번 더 확인해 제거합니다.
    """

    if hasattr(strategy_report, "model_dump"):
        data = strategy_report.model_dump()
    elif isinstance(strategy_report, dict):
        data = dict(strategy_report)
    else:
        raise TypeError(
            "sanitize_strategy_report는 pydantic 모델 또는 dict만 "
            f"처리할 수 있습니다: {type(strategy_report)!r}"
        )

    _strip_pii_keys(data)

    student_summary = data.get("student_summary")

    if isinstance(student_summary, dict):
        _strip_pii_keys(student_summary)

    return data