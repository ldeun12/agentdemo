import json
from pathlib import Path

from pydantic import ValidationError

from src.gemini_client import GeminiError, generate_json
from src.schemas import TranscriptData


class TranscriptExtractionError(RuntimeError):
    """성적증명서 분석 오류."""


def build_transcript_prompt(department_hint: str | None = None) -> str:
    hint = department_hint.strip() if department_hint else "없음"
    return f"""
첨부 PDF는 순천대학교 성적증명서입니다. 문서에 실제로 보이는 내용만
읽어 아래 JSON으로 반환하세요. 가려졌거나 불명확한 값은 추측하지 마세요.
이름은 추출하지 말고, 학번은 입학연도 확인에만 사용합니다.

{{
  "department": "학과/학부/전공명 또는 빈 문자열",
  "student_number": "학번 또는 null",
  "admission_year": 2023,
  "total_earned_credits": 0,
  "major_credits": 0,
  "general_education_credits": 0,
  "general_education_common_credits": 0,
  "general_education_advanced_credits": 0,
  "general_elective_credits": 0,
  "gpa": null,
  "latest_semester_gpa": null,
  "latest_semester_credits": null,
  "completed_courses": [
    {{"course_name":"과목명","credits":3,"grade":"A+","category":"전공선택","semester":"2025-1"}}
  ]
}}

규칙:
- JSON만 반환하고 모든 이수 과목을 포함합니다.
- 학점 취득내역 요약표의 값을 우선하고 임의로 합산하지 않습니다.
- 전필+전선은 major_credits, 일선은 general_elective_credits입니다.
- 최근 학기의 학점 계와 평점 평균을 latest_semester 항목에 넣습니다.
- 학과 힌트는 참고만 하며 PDF와 다르면 PDF를 우선합니다: {hint}
""".strip()


def extract_transcript_data(
    pdf_path: str | Path,
    department_hint: str | None = None,
    client=None,
) -> TranscriptData:
    path = Path(pdf_path).resolve()
    if not path.exists() or path.suffix.lower() != ".pdf":
        raise TranscriptExtractionError("분석할 PDF 파일을 찾을 수 없습니다.")

    try:
        parsed = generate_json(
            build_transcript_prompt(department_hint),
            pdf_bytes=path.read_bytes(),
            client=client,
        )
        # 로그에 학번 등 개인정보 원문을 출력하지 않습니다.
        safe_log = {k: v for k, v in parsed.items() if k != "student_number"}
        print(json.dumps(safe_log, ensure_ascii=False, indent=2))
        return TranscriptData.model_validate(parsed)
    except (GeminiError, ValidationError) as error:
        raise TranscriptExtractionError(f"성적증명서 분석에 실패했습니다: {error}") from error
