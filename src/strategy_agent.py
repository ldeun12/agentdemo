import json

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError

from config.settings import settings
from src.schemas import (
    CurriculumRequirements,
    GraduationStatus,
    StrategyReport,
    TranscriptData,
)
from src.scholarship_guide import (
    ScholarshipGuideNotFoundError,
    get_scholarship_guide_text,
)


class StrategyAgentError(RuntimeError):
    """Claude 전략 생성 과정에서 발생한 오류."""


def build_strategy_prompt(
    transcript: TranscriptData,
    requirements: CurriculumRequirements,
    graduation_status: GraduationStatus,
    user_request: str | None = None,
    scholarship_guide_text: str | None = None,
) -> str:
    """추천 과목과 장학금 전략 생성을 위한 프롬프트입니다."""

    input_data = {
        "student_summary": transcript.model_dump(),
        "curriculum_requirements": requirements.model_dump(
            exclude={"source_context"}
        ),
        "graduation_status": graduation_status.model_dump(),
        "user_request": user_request,
    }

    guide_section = (
        f"""
장학금 공식 안내 (순천대학교 학사안내 페이지 원문, 아래 내용에
실제로 있는 기준만 사실로 취급하세요):
---
{scholarship_guide_text}
---
"""
        if scholarship_guide_text
        else "장학금 안내 자료를 가져오지 못했습니다. 장학금 관련 판단은 전부 '추가 확인 필요'로 답하세요."
    )

    return f"""
다음 학생의 이수 현황과 공식 졸업요건을 바탕으로
추천 과목과 장학금 준비 전략을 JSON으로 작성하세요.

입력에 없는 장학금 선발 기준이나 과목을 추측하지 마세요.
부족한 필수 과목을 가장 먼저 추천하세요.

{guide_section}

반드시 다음 JSON 구조만 반환하세요.

{{
  "recommended_courses": [
    {{
      "course_name": "과목명",
      "category": "필수 또는 null",
      "priority": 1,
      "reason": "추천 이유"
    }}
  ],
  "scholarship_strategy": {{
    "current_gpa": 0,
    "possibility": "추가 확인 필요",
    "advice": "전체적인 준비 방향 한두 문장",
    "requirements_to_check": ["확인할 조건"],
    "recommended_scholarships": [
      {{
        "name": "장학금명",
        "possibility": "가능성 있음, 가능성 낮음, 추가 확인 필요 중 하나",
        "reason": "이 장학금을 골라준 이유와 판단 근거",
        "requirements_to_check": ["안내문에 없어서 추가로 확인해야 할 조건"]
      }}
    ]
  }},
  "custom_answer": null
}}

규칙:
1. 설명이나 Markdown 코드 블록을 작성하지 마세요.
2. 추천 우선순위는 1부터 시작하세요.
3. 장학금 수혜 가능성을 확정적으로 표현하지 마세요.
4. 개인정보를 응답에 포함하지 마세요.
5. 미이수 필수 과목을 다 채웠거나 없다면, curriculum_requirements의
   elective_courses(전공선택 과목 목록)에 실제로 있는 과목명 중에서
   student_summary.completed_courses에 아직 없는 과목을 골라
   추천하세요. "학과 커리큘럼 확인 필요", "구체적 과목명은 확인 필요"
   처럼 실제 과목명 없이 얼버무리지 마세요. elective_courses에
   있는 과목명을 그대로 사용하세요.
6. 과목을 고를 때는 이미 들은 과목들의 흐름(예: 프로그래밍 기초를
   들었다면 관련 심화 과목, 특정 트랙 과목을 연달아 들었다면 같은
   트랙의 다음 과목)을 참고해서 자연스럽게 이어지는 과목을
   우선하세요. reason에 왜 그 과목을 골랐는지 학생의 이수 이력과
   연결해서 설명하세요.
7. elective_courses가 비어 있어 추천할 과목이 정말 없는 경우에만
   과목명 없이 방향성만 안내하세요.
8. 장학금 판단은 반드시 위 "장학금 공식 안내" 섹션에 실제로 적힌
   기준(직전학기 평점평균, 직전학기 취득학점 등)만 근거로 삼으세요.
   student_summary.latest_semester_gpa(직전학기 평점평균)와
   latest_semester_credits(직전학기 취득학점)를 기준과 비교하세요.
   전체 누적 평점(gpa)은 장학금 기준이 아니므로 판단에 쓰지 마세요.
9. 안내 텍스트에 있는 조건을 학생이 충족하면 "가능성 있음"처럼
   명확하게 안내하고, 학과별 세부 기준처럼 안내문에 없는 정보가
   필요한 조건만 "추가 확인 필요"로 표시하세요. 안내문에 있는데도
   전부 "추가 확인 필요"로 뭉뚱그리지 마세요.
10. 안내문에 없는 장학금명을 지어내지 마세요.
12. recommended_scholarships에는 "장학금 공식 안내"에 나오는
    장학금 종류(학업성적우수자, 국가유공자, 경제적 사정이 곤란한
    자, 체육특기자, 가족 2인이상, 장애인장학금 등) 중 학생의
    상황과 관련 있어 보이는 것들을 각각 하나의 항목으로 넣으세요.
    advice 문단 하나에 전부 몰아서 쓰지 말고, 장학금별로 나눠서
    possibility와 reason을 따로 작성하세요.
11. reason, advice 같은 사람이 읽는 문장에는 절대 입력 JSON의
    필드 이름(예: missing_required_courses, completed_courses,
    elective_courses, curriculum_requirements 등)을 그대로
    쓰지 마세요. "missing_required_courses에 포함된 과목" 대신
    "졸업에 필요한 필수 과목" 처럼 자연스러운 한국어로 풀어서
    설명하세요.

입력:
{json.dumps(input_data, ensure_ascii=False)}
""".strip()


def generate_strategy_report(
    transcript: TranscriptData,
    requirements: CurriculumRequirements,
    graduation_status: GraduationStatus,
    user_request: str | None = None,
    client=None,
) -> StrategyReport:
    """Claude로 추천 과목과 장학금 전략을 생성합니다."""

    if not settings.bedrock_model_id:
        raise StrategyAgentError(
            "BEDROCK_MODEL_ID가 설정되지 않았습니다."
        )

    if client is None:
        client = boto3.client(
            "bedrock-runtime",
            region_name=settings.aws_region,
        )

    try:
        scholarship_guide_text = get_scholarship_guide_text()
        print("✅ 장학금 안내 PDF를 불러왔습니다.")

    except ScholarshipGuideNotFoundError as error:
        scholarship_guide_text = None
        print(f"⚠️ 장학금 안내 PDF를 불러오지 못했습니다: {error}")

    prompt = build_strategy_prompt(
        transcript=transcript,
        requirements=requirements,
        graduation_status=graduation_status,
        user_request=user_request,
        scholarship_guide_text=scholarship_guide_text,
    )

    try:
        response = client.converse(
            modelId=settings.bedrock_model_id,
            system=[
                {
                    "text": (
                        "당신은 대학 졸업계획과 장학금 준비를 "
                        "돕는 신중한 학업 전략 도우미입니다."
                    )
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": [{"text": prompt}],
                }
            ],
            inferenceConfig={
                "maxTokens": 8192,
            },
        )

    except (ClientError, BotoCoreError) as error:
        raise StrategyAgentError(
            f"Claude 호출 중 오류가 발생했습니다: {error}"
        ) from error

    content_blocks = (
        response.get("output", {})
        .get("message", {})
        .get("content", [])
    )

    response_text = "".join(
        str(block.get("text", ""))
        for block in content_blocks
        if isinstance(block, dict)
    ).strip()

    start_index = response_text.find("{")
    end_index = response_text.rfind("}")

    if start_index == -1 or end_index == -1:
        raise StrategyAgentError(
            "Claude 응답에서 JSON을 찾지 못했습니다."
        )

    try:
        generated_data = json.loads(
            response_text[start_index:end_index + 1]
        )

        report_data = {
            "student_summary": transcript,
            "curriculum_requirements": requirements,
            "graduation_status": graduation_status,
            "recommended_courses": generated_data.get(
                "recommended_courses",
                [],
            ),
            "scholarship_strategy": generated_data.get(
                "scholarship_strategy",
                {},
            ),
            "user_request": user_request,
            "custom_answer": generated_data.get(
                "custom_answer"
            ),
        }

        return StrategyReport.model_validate(report_data)

    except (json.JSONDecodeError, ValidationError) as error:
        raise StrategyAgentError(
            "Claude 응답이 전략 리포트 구조와 일치하지 않습니다."
        ) from error