"""
Bedrock Knowledge Base의 벡터 검색은 이 편람처럼 학과가 30개나
한 표에 몰려 있는 문서에서는 청킹 과정에서 표가 깨져
엉뚱한 학과 데이터를 가져오는 문제가 있습니다.

이 모듈은 검색(semantic search) 대신, PDF의 표를 직접
프로그램으로 파싱해서 학과명으로 정확히 일치하는 행만 뽑아냅니다.
편람의 표 구조가 매년 거의 동일하다는 전제 하에 동작하며,
학과를 찾지 못하면 None을 반환해 RAG 방식으로 폴백할 수 있게 합니다.
"""

import io
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional

import pdfplumber

from config.settings import settings
from src.schemas import CurriculumRequirements

# 편람 PDF에 포함된 커스텀(Type3) 폰트 때문에 pdfminer가
# "Could not get FontBBox..." 경고를 수백 줄씩 찍습니다.
# 표 파싱 자체에는 영향이 없으므로 로그 레벨을 올려 조용히 시킵니다.
logging.getLogger("pdfminer").setLevel(logging.ERROR)


class CurriculumPdfNotFoundError(RuntimeError):
    """로컬에서 교육과정 편람 PDF를 찾지 못했을 때 발생합니다."""


_COURSE_HEADER_KEYS = [
    "이수학년",
    "개설학기",
    "연번",
    "과목코드",
    "교과목명",
    "학점",
    "이론",
    "실습",
    "이수구분",
]


def _clean(value: Any) -> str:
    """
    공백/줄바꿈을 제거하고, 유니코드 정규화(NFC)를 적용해 셀 값을
    비교하기 쉽게 만듭니다.

    브라우저 입력창에서 온 한글과 PDF에서 추출한 한글이 화면에는
    똑같아 보여도 내부적으로 다른 유니코드 형태(NFC/NFD)로 저장되어
    있으면 문자열 비교가 실패할 수 있습니다. NFC로 통일해 이를 방지합니다.
    """

    if value is None:
        return ""

    text = re.sub(r"\s+", "", str(value))

    return unicodedata.normalize("NFC", text)


def _parse_number(value: Any, default: float = 0.0) -> float:
    """'72이상', '15~31', '130' 등에서 첫 번째 숫자를 뽑아냅니다."""

    if value is None:
        return default

    match = re.search(r"-?\d+(\.\d+)?", str(value))

    return float(match.group()) if match else default


def _clean_range_text(value: Any) -> str:
    """
    '72이상', '30~46' 같은 편람 원본 표기를 화면에 보여주기 좋게
    다듬습니다 (줄바꿈/여백만 정리하고, "이상"이나 "~" 같은 표기는
    그대로 유지합니다).
    """

    if value is None:
        return ""

    return re.sub(r"\s+", "", str(value))


def get_curriculum_pdf_path(admission_year: Optional[int] = None) -> Path:
    """
    data/public 폴더의 교육과정편람 PDF 경로를 반환합니다.

    학과별 교육과정은 매년 바뀌고, 학생은 원칙적으로 본인 입학연도의
    교육과정을 따릅니다. 그래서 여러 연도의 편람이 함께 업로드되어
    있을 수 있는 경우, "가장 최근에 업로드된 파일"이 아니라
    "학생의 입학연도가 파일명에 포함된 파일"을 우선 선택합니다.
    일치하는 파일이 없으면 가장 최근에 수정된 파일로 폴백합니다.

    입학연도가 파일명에 있으면 해당 연도 파일을 우선합니다.
    """

    public_dir = settings.data_dir / "public"
    pdfs = [p for p in public_dir.glob("*.pdf") if "장학" not in p.name and "scholarship" not in p.name.lower()]
    if not pdfs:
        raise CurriculumPdfNotFoundError(
            f"{public_dir} 폴더에 교육과정편람 PDF가 없습니다."
        )
    if admission_year:
        matches = [p for p in pdfs if str(admission_year) in p.name]
        if matches:
            return max(matches, key=lambda p: p.stat().st_mtime)
    return max(pdfs, key=lambda p: p.stat().st_mtime)


def _looks_like_summary_table(table: Optional[list]) -> bool:
    """이 표가 학과(부)/전공별 졸업학점 구성표(16개 열)인지 확인합니다."""

    return bool(table) and len(table[0]) == 16


def _find_summary_row(
    pdf: Any,
    department: str,
) -> Optional[list]:
    """학과(부)/전공별 졸업학점 구성표에서 학과명이 일치하는 행을 찾습니다."""

    target = _clean(department)
    section_title = _clean("전공별 졸업학점 구성표")

    in_section = False
    candidate_rows: list[list] = []

    for page in pdf.pages:
        text = _clean(page.extract_text() or "")

        if not in_section:
            if section_title not in text:
                continue

            in_section = True

        table = page.extract_table()

        # 이 표는 여러 페이지에 걸쳐 있고, 이어지는 페이지에는 제목이
        # 다시 나오지 않습니다. 표 형태(16개 열)가 계속되는 동안은
        # 같은 표로 보고 계속 확인합니다.
        if not _looks_like_summary_table(table):
            break

        for row in table:
            if len(row) > 14 and row[1]:
                candidate_rows.append(row)

    # 1순위: 완전히 일치
    for row in candidate_rows:
        if _clean(row[1]) == target:
            return row

    # 2순위: 부분 일치. 성적표에는 "우주항공·첨단소재스쿨 인공지능공학부"처럼
    # 소속 스쿨/단과대학명이 학과명 앞에 붙어서 나오는 경우가 있습니다.
    # 편람의 학과명이 인식된 학과명 문자열에 포함되어 있으면(혹은 그
    # 반대면) 같은 학과로 보고 인정합니다. 너무 짧은 이름끼리의 우연한
    # 포함은 걸러내기 위해 최소 길이를 둡니다.
    MIN_PARTIAL_MATCH_LENGTH = 4
    partial_matches = [
        row
        for row in candidate_rows
        if len(_clean(row[1])) >= MIN_PARTIAL_MATCH_LENGTH
        and (_clean(row[1]) in target or target in _clean(row[1]))
    ]

    if len(partial_matches) == 1:
        matched_row = partial_matches[0]
        print(
            f"  ℹ️ '{department}'와 정확히 일치하는 학과는 없지만, "
            f"'{matched_row[1]}'과(와) 부분 일치하여 이 학과로 처리합니다."
        )
        return matched_row

    if len(partial_matches) > 1:
        print(
            f"  ⚠️ '{department}'와 부분 일치하는 학과가 여러 개라 "
            "확정할 수 없습니다: "
            f"{[row[1] for row in partial_matches]}"
        )

    return None


def _is_course_table(table: Optional[list]) -> bool:
    if not table or not table[0]:
        return False

    header = [_clean(cell) for cell in table[0]]

    return header == _COURSE_HEADER_KEYS


def _find_department_course_pages(
    pdf: Any,
    department: str,
) -> list[int]:
    """학과 전공 교과목 표가 걸쳐 있는 페이지 번호(0-indexed)를 찾습니다."""

    started = False
    pages: list[int] = []

    for index, page in enumerate(pdf.pages):
        text = page.extract_text() or ""
        table = page.extract_table()

        if not started:
            if department in text and _is_course_table(table):
                started = True
                pages.append(index)

            continue

        if not _is_course_table(table):
            break

        assert table is not None  # _is_course_table already confirmed this

        first_data_row = next(
            (row for row in table[1:] if row and row[2]),
            None,
        )

        # 연번이 1로 초기화되면 다음 학과의 표가 시작된 것입니다.
        if first_data_row and _clean(first_data_row[2]) == "1":
            break

        pages.append(index)

    return pages


def _parse_department_courses(
    pdf: Any,
    department: str,
) -> list[dict[str, Any]]:
    """학과 전공 교과목을 표에서 직접 추출합니다."""

    courses: list[dict[str, Any]] = []

    for index in _find_department_course_pages(pdf, department):
        table = pdf.pages[index].extract_table()

        if table is None:
            continue

        for row in table[1:]:
            if not row or not row[2] or not row[4]:
                continue

            # 연번 칸이 숫자가 아니면 표 하단의 합계 행이므로 건너뜁니다.
            if not _clean(row[2]).isdigit():
                continue

            course_name = str(row[4]).strip()
            credits = _parse_number(row[5])
            is_required = (row[8] or "").strip() == "필수"

            courses.append(
                {
                    "course_name": course_name,
                    "credits": credits,
                    "category": "전공필수" if is_required else "전공선택",
                }
            )

    return courses


def parse_curriculum_requirements(
    department: str,
    pdf_path: Optional[Path] = None,
    admission_year: Optional[int] = None,
) -> Optional[CurriculumRequirements]:
    """
    교육과정편람 PDF를 직접 파싱해 CurriculumRequirements를 만듭니다.
    학과를 표에서 찾지 못하면 None을 반환합니다 (RAG 폴백용).
    """

    resolved_path = pdf_path or get_curriculum_pdf_path(
        admission_year=admission_year
    )

    with pdfplumber.open(resolved_path) as pdf:
        summary_row = _find_summary_row(pdf, department)

        if summary_row is None:
            return None

        # 과목 목록 페이지는 편람에 실제로 적힌 짧은 학과명(예:
        # "인공지능공학부")만으로 찾습니다. 입력값이 "우주항공·첨단
        # 소재스쿨 인공지능공학부"처럼 소속명이 붙은 긴 문자열이면
        # 과목 목록 페이지 어디에도 그 전체 문구가 그대로 나오지
        # 않아 못 찾을 수 있으므로, 요약표에서 실제로 매칭된 학과명을
        # 그대로 사용합니다.
        canonical_department = _clean(summary_row[1])
        courses = _parse_department_courses(pdf, canonical_department)

    required_courses = [
        course["course_name"]
        for course in courses
        if course["category"] == "전공필수"
    ]

    elective_courses = [
        course["course_name"]
        for course in courses
        if course["category"] == "전공선택"
    ]

    # 표 컬럼 순서 (요약표 데이터 행 기준, 셀 좌표로 직접 검증함):
    #  3=기초, 4=핵심, 5=글로벌의사소통, 6=인성
    #     -> 공통교양 필수 이수 항목 (고정값 합산).
    #  7=브릿지: "공통" 헤더 아래 있지만 괄호 표기(예: "(2)")로
    #     구분되어 있고, 실제로는 선택적으로 들을 수 있는 항목이라
    #     필수 이수 학점 계산에는 포함하지 않습니다. (index9 교양
    #     합계 최솟값과 대조해보면, 브릿지를 빼고 더해야 정확히
    #     일치합니다: 기초+핵심+글로벌+인성 + 심화(최소) = 합계(최소))
    #  8=심화(학과지정) -> 별도 컬럼. 고정값이 아니라 "15~31"처럼
    #     범위로 표기되어 있어, 범위의 최솟값을 기준값으로 씁니다.
    #  9=교양 합계 범위(예: "30~46"). 개별 항목을 단순히 더한 값과
    #     정확히 일치하지 않을 수 있어(학과별 대체이수 등 정책 예외),
    #     교양 총계는 이 합계 컬럼의 최솟값을 그대로 사용합니다.
    common_ge_credits = sum(
        _parse_number(summary_row[index]) for index in (3, 4, 5, 6)
    )
    advanced_ge_credits = _parse_number(
        str(summary_row[8]).split("~")[0] if summary_row[8] else "0"
    )
    total_ge_credits = _parse_number(
        str(summary_row[9]).split("~")[0] if summary_row[9] else "0"
    )

    major_range_text = _clean_range_text(summary_row[13])
    ge_total_range_text = _clean_range_text(summary_row[9])
    ge_advanced_range_text = _clean_range_text(summary_row[8])

    return CurriculumRequirements(
        required_total_credits=_parse_number(summary_row[14]),
        required_major_credits=_parse_number(summary_row[13]),
        required_general_education_credits=total_ge_credits,
        required_general_education_common_credits=common_ge_credits,
        required_general_education_advanced_credits=advanced_ge_credits,
        required_major_credits_range=major_range_text,
        required_general_education_credits_range=ge_total_range_text,
        required_general_education_advanced_credits_range=ge_advanced_range_text,
        required_courses=required_courses,
        elective_courses=elective_courses,
        source_context=(
            f"2023학년도 순천대학교 교육과정편람 PDF의 졸업요건 요약표와 "
            f"'{department}' 전공 교과목 편성표에서 직접 파싱한 결과입니다."
        ),
    )
