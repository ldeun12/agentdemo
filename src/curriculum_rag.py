from typing import Any

from pydantic import ValidationError

from src.curriculum_table_parser import (
    CurriculumPdfNotFoundError,
    get_curriculum_pdf_path,
    parse_curriculum_requirements,
)
from src.gemini_client import GeminiError, generate_json
from src.schemas import CurriculumRequirements


class CurriculumSearchError(RuntimeError):
    """로컬 교육과정편람 검색 오류."""


def build_curriculum_queries(department: str) -> list[str]:
    department = department.strip()
    if not department:
        raise ValueError("학과를 확인할 수 없습니다.")
    return [
        f"{department} 졸업학점 전공학점 교양학점",
        f"{department} 전공필수 과목",
        f"{department} 전공선택 과목",
    ]


def build_curriculum_prompt(department: str, admission_year: int | None) -> str:
    return f"""
첨부된 순천대학교 공식 교육과정편람 PDF에서 다음 학생에게 적용되는 내용만 찾으세요.
학과: {department}
입학연도: {admission_year or '문서에서 가장 적절한 기준'}

다음 JSON만 반환하세요.
{{
 "required_total_credits":0,
 "required_major_credits":0,
 "required_general_education_credits":0,
 "required_general_education_common_credits":0,
 "required_general_education_advanced_credits":0,
 "required_major_credits_range":"",
 "required_general_education_credits_range":"",
 "required_general_education_advanced_credits_range":"",
 "required_courses":[],
 "elective_courses":[]
}}

학과명이 정확히 일치하는 표와 해당 입학연도 기준만 사용하세요.
확인할 수 없는 값은 0 또는 빈 목록으로 두고 추측하지 마세요.
""".strip()


def search_curriculum_requirements(
    department: str,
    admission_year: int | None = None,
    client=None,
    **_: Any,
) -> dict[str, Any]:
    """기존 호출 호환용: 로컬 PDF를 Gemini로 구조화한 결과를 반환합니다."""
    try:
        pdf_path = get_curriculum_pdf_path(admission_year)
        parsed = generate_json(
            build_curriculum_prompt(department, admission_year),
            pdf_bytes=pdf_path.read_bytes(),
            client=client,
        )
    except (CurriculumPdfNotFoundError, GeminiError) as error:
        raise CurriculumSearchError(str(error)) from error
    return {"department": department, "parsed": parsed, "source": str(pdf_path)}


def structure_curriculum_requirements(
    search_result: dict[str, Any],
    client=None,
) -> CurriculumRequirements:
    del client
    try:
        data = dict(search_result.get("parsed", {}))
        data["source_context"] = f"로컬 공식 편람: {search_result.get('source', '')}"
        return CurriculumRequirements.model_validate(data)
    except ValidationError as error:
        raise CurriculumSearchError("교육과정 결과 구조가 올바르지 않습니다.") from error


def get_curriculum_requirements(
    department: str,
    admission_year: int | None = None,
    retrieval_client=None,
    model_client=None,
) -> CurriculumRequirements:
    """우선 PDF 표를 로컬 파싱하고, 실패할 때만 Gemini로 PDF를 읽습니다."""
    try:
        direct = parse_curriculum_requirements(department, admission_year=admission_year)
        if direct is not None:
            return direct
    except Exception as error:
        print(f"로컬 표 직접 파싱 실패, Gemini로 전환: {error}")

    result = search_curriculum_requirements(
        department,
        admission_year=admission_year,
        client=model_client or retrieval_client,
    )
    return structure_curriculum_requirements(result)
