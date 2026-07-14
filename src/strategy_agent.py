import json

from pydantic import ValidationError

from src.gemini_client import GeminiError, generate_json
from src.schemas import CurriculumRequirements, GraduationStatus, StrategyReport, TranscriptData
from src.scholarship_guide import ScholarshipGuideNotFoundError, get_scholarship_guide_text


class StrategyAgentError(RuntimeError):
    """전략 리포트 생성 오류."""


def build_strategy_prompt(
    transcript: TranscriptData,
    requirements: CurriculumRequirements,
    graduation_status: GraduationStatus,
    user_request: str | None = None,
    scholarship_guide_text: str | None = None,
) -> str:
    data = {
        "student_summary": transcript.model_dump(),
        "curriculum_requirements": requirements.model_dump(exclude={"source_context"}),
        "graduation_status": graduation_status.model_dump(),
        "user_request": user_request,
    }
    guide = scholarship_guide_text or "자료 없음: 모든 장학금 판단을 추가 확인 필요로 표시"
    return f"""
학생의 이수 현황과 공식 졸업요건을 바탕으로 학업 전략을 JSON으로 작성하세요.
입력에 없는 과목, 장학금명 또는 선발 기준을 만들지 마세요. 개인정보는 쓰지 마세요.
미이수 필수 과목을 먼저 추천하고, 그다음 실제 elective_courses 중 미이수 과목을 추천하세요.
장학금은 누적 GPA가 아니라 직전학기 평점과 취득학점을 공식 안내와 비교하세요.

반환 구조:
{{
 "recommended_courses":[{{"course_name":"과목명","category":"필수 또는 null","priority":1,"reason":"이유"}}],
 "scholarship_strategy":{{
   "current_gpa":0,"possibility":"가능성 있음/가능성 낮음/추가 확인 필요",
   "advice":"준비 방향","requirements_to_check":[],
   "recommended_scholarships":[{{"name":"장학금명","possibility":"판단","reason":"근거","requirements_to_check":[]}}]
 }},
 "custom_answer":null
}}

공식 장학금 안내:
{guide}

분석 입력:
{json.dumps(data, ensure_ascii=False)}
""".strip()


def generate_strategy_report(
    transcript: TranscriptData,
    requirements: CurriculumRequirements,
    graduation_status: GraduationStatus,
    user_request: str | None = None,
    client=None,
) -> StrategyReport:
    try:
        guide = get_scholarship_guide_text()
    except ScholarshipGuideNotFoundError:
        guide = None

    try:
        generated = generate_json(
            build_strategy_prompt(transcript, requirements, graduation_status, user_request, guide),
            client=client,
        )
        return StrategyReport.model_validate({
            "student_summary": transcript,
            "curriculum_requirements": requirements,
            "graduation_status": graduation_status,
            "recommended_courses": generated.get("recommended_courses", []),
            "scholarship_strategy": generated.get("scholarship_strategy", {}),
            "user_request": user_request,
            "custom_answer": generated.get("custom_answer"),
        })
    except (GeminiError, ValidationError) as error:
        raise StrategyAgentError(f"전략 리포트 생성에 실패했습니다: {error}") from error
