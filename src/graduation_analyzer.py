import unicodedata
from difflib import SequenceMatcher

from src.schemas import (
    CurriculumRequirements,
    GraduationStatus,
    TranscriptData,
)

# 이 정도 유사도 이상이면 같은 과목의 표기 차이(OCR 오차 등)로 보고
# "이수함"으로 인정합니다. 1.0이 완전 일치입니다.
FUZZY_MATCH_THRESHOLD = 0.85


def normalize_course_name(course_name: str) -> str:
    """
    과목명의 공백과 대소문자 차이를 제거하고 유니코드를 NFC로
    정규화합니다.

    성적증명서(Claude가 PDF에서 읽은 값)와 교육과정편람(PDF 표에서
    직접 파싱한 값)의 한글이 화면엔 똑같아 보여도 내부적으로 다른
    유니코드 형태(NFC/NFD)로 인코딩되어 있으면 단순 공백 제거만으로는
    같은 문자열로 인식되지 않을 수 있습니다.
    """

    cleaned = "".join(course_name.split()).casefold()

    return unicodedata.normalize("NFC", cleaned)


def _is_fuzzy_match(name_a: str, name_b: str) -> bool:
    """
    두 과목명이 완전히 같지는 않아도 사실상 같은 과목으로 볼 수
    있는지 판단합니다. "어드벤처디자인" vs "어드벤쳐디자인"처럼
    OCR 과정에서 한두 글자가 비슷한 다른 글자로 잘못 인식되는
    경우를 잡아내기 위한 것입니다.
    """

    if not name_a or not name_b:
        return False

    ratio = SequenceMatcher(None, name_a, name_b).ratio()

    return ratio >= FUZZY_MATCH_THRESHOLD


def calculate_remaining(
    required: float,
    current: float,
) -> float:
    """필요 학점에서 현재 학점을 빼되 음수는 반환하지 않습니다."""

    return max(required - current, 0)


def analyze_graduation_status(
    transcript: TranscriptData,
    requirements: CurriculumRequirements,
) -> GraduationStatus:
    """성적증명서와 교육과정 졸업요건을 비교합니다."""

    completed_course_names = {
        normalize_course_name(course.course_name)
        for course in transcript.completed_courses
    }

    missing_required_courses = []

    for course_name in requirements.required_courses:
        normalized_required = normalize_course_name(course_name)

        if normalized_required in completed_course_names:
            continue

        # 정확히 일치하는 건 없지만, 한두 글자 차이 정도로 매우 비슷한
        # 과목명이 이수 목록에 있다면 표기 차이로 보고 이수한 것으로
        # 인정합니다 (완전히 다른 과목일 가능성도 있으니, 화면에서는
        # 이 경우를 별도로 안내하는 것을 권장합니다).
        if any(
            _is_fuzzy_match(normalized_required, completed_name)
            for completed_name in completed_course_names
        ):
            continue

        missing_required_courses.append(course_name)

    return GraduationStatus(
        current_total_credits=transcript.total_earned_credits,
        required_total_credits=requirements.required_total_credits,
        remaining_total_credits=calculate_remaining(
            requirements.required_total_credits,
            transcript.total_earned_credits,
        ),
        current_major_credits=transcript.major_credits,
        required_major_credits=requirements.required_major_credits,
        remaining_major_credits=calculate_remaining(
            requirements.required_major_credits,
            transcript.major_credits,
        ),
        required_major_credits_range=requirements.required_major_credits_range,
        current_general_education_credits=(
            transcript.general_education_credits
        ),
        required_general_education_credits=(
            requirements.required_general_education_credits
        ),
        remaining_general_education_credits=calculate_remaining(
            requirements.required_general_education_credits,
            transcript.general_education_credits,
        ),
        required_general_education_credits_range=(
            requirements.required_general_education_credits_range
        ),
        current_general_education_common_credits=(
            transcript.general_education_common_credits
        ),
        required_general_education_common_credits=(
            requirements.required_general_education_common_credits
        ),
        remaining_general_education_common_credits=calculate_remaining(
            requirements.required_general_education_common_credits,
            transcript.general_education_common_credits,
        ),
        current_general_education_advanced_credits=(
            transcript.general_education_advanced_credits
        ),
        required_general_education_advanced_credits=(
            requirements.required_general_education_advanced_credits
        ),
        remaining_general_education_advanced_credits=calculate_remaining(
            requirements.required_general_education_advanced_credits,
            transcript.general_education_advanced_credits,
        ),
        required_general_education_advanced_credits_range=(
            requirements.required_general_education_advanced_credits_range
        ),
        missing_required_courses=missing_required_courses,
    )