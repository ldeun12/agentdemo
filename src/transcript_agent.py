import json
from pathlib import Path

from pydantic import ValidationError

from src.gemini_client import GeminiError, generate_json
from src.schemas import TranscriptData


class TranscriptExtractionError(RuntimeError):
    """성적증명서 분석 오류."""


def build_transcript_prompt(department_hint: str | None = None) -> str:
    return f"""
첨부 PDF는 순천대학교 성적증명서입니다. 문서에 실제로 보이는 내용만
읽어 아래 JSON으로 반환하세요. 가려졌거나 불명확한 값은 추측하지 마세요.
이름은 추출하지 말고, 학번은 입학연도 확인에만 사용합니다.

{{
  "department": "학과/학부/전공명 또는 빈 문자열",
  "student_number": "학번 또는 null",
  "admission_year": "null",
  "total_earned_credits": 0,
  "major_credits": 0,
  "general_education_credits": 0,
  "general_education_common_credits": 0,
  "general_education_advanced_credits": 0,
  "general_elective_credits": 0,
  "gpa": null,
  "latest_semester_gpa": "null",
  "latest_semester_credits": "null",
  "completed_courses": [
    {{
      "course_name": "과목명",
      "credits": 0,
      "grade": "성적 또는 null",
      "category": "전공필수, 전공선택, 교양 등 또는 null",
      "semester": "이수 학기 또는 null"
    }}
  ]
}}

규칙:
1. 설명이나 Markdown 코드 블록을 쓰지 말고 JSON만 반환하세요.
2. 숫자 필드는 문자열이 아닌 숫자로 반환하세요.
3. 정보가 없으면 학점은 0, 문자열은 null 또는 빈 문자열로 반환하세요.
4. 과목을 중복해서 넣지 마세요. PDF에 보이는 모든 이수 과목을 빠짐없이 포함하세요.
5. 총 취득학점을 과목 합계로 임의 계산하지 말고, 문서에 표기된 값을 그대로 사용하세요.
6. "학과", "학부", "전공", "소속" 항목 주변을 확인하여 department를 추출하세요.
7. department에는 단과대학명이 아니라 학생의 실제 학과·학부·전공명을 반환하세요.
8. 표와 본문 중 한쪽에만 학과 정보가 있어도 반드시 확인하세요.
9. "department" 등 필드에 이 지시문의 설명 문구(예: "학과명 또는 빈 문자열")를
   그대로 베끼지 말고, 반드시 PDF에서 읽은 실제 값을 넣으세요.
10. admission_year(입학연도)는 "입학연월일" 항목이나 학번의 앞 4자리
    숫자에서 확인하세요. 두 값이 다르면 "입학연월일"을 우선하세요.
11. 학점 관련 숫자는 성적표의 "학점 취득내역"이라는 별도 요약 표에
    정리되어 있는 경우가 많습니다. 과목을 하나씩 세어 계산하지 말고
    반드시 그 요약 표에 적힌 숫자를 그대로 사용하세요.
12. 그 요약 표는 보통 "전필"(전공필수), "전선"(전공선택),
    "일선"(일반선택), "교기"(교양기초), "교핵"(교양핵심),
    "교글"(교양글로벌의사소통), "교인"(교양인성), "심교"(심화교양)
    같은 약어 열로 구성됩니다. 다음과 같이 채우세요:
    - major_credits = "전필" + "전선"의 합
    - general_elective_credits = "일선" 열의 값
    - general_education_common_credits = "교기"+"교핵"+"교글"+"교인"
      등 심화를 제외한 교양 관련 열의 합 (공통교양)
    - general_education_advanced_credits = "심교" 열의 값 (심화교양)
    - general_education_credits = 위 공통교양 + 심화교양의 합
    표에 있는 숫자인데 0으로 반환하지 마세요.
13. latest_semester_gpa, latest_semester_credits는 "학점 취득내역"
    요약 표가 아니라, 성적표에 학기별로 나뉘어 있는 과목 목록 중
    가장 마지막(최근) 학기 구획의 "평점 평균"과 "학점 계"를
    찾아서 채우세요. 전체 누적 평점(gpa)과는 다른 값입니다.

{hint_line}
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
