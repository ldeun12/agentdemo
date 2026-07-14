import re
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


def _coerce_optional_float(value: Any) -> Optional[float]:
    """
    "3", "3.0", "3(재수강)", "", None 등 다양한 형태로 오는 값을
    가능하면 float로, 불가능하면 None으로 변환합니다.
    """

    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()

    if not text:
        return None

    match = re.search(r"-?\d+(\.\d+)?", text)

    if not match:
        return None

    return float(match.group())


class Course(BaseModel):
    """성적증명서에서 추출한 과목 한 개의 정보."""

    course_name: str = "이름 미확인 과목"
    credits: float = 0
    grade: Optional[str] = None
    category: Optional[str] = None
    semester: Optional[str] = None

    @field_validator("course_name", mode="before")
    @classmethod
    def _default_course_name(cls, value: Any) -> str:
        if value is None:
            return "이름 미확인 과목"

        text = str(value).strip()

        return text or "이름 미확인 과목"

    @field_validator("credits", mode="before")
    @classmethod
    def _parse_credits(cls, value: Any) -> float:
        return _coerce_optional_float(value) or 0


class TranscriptData(BaseModel):
    """성적증명서 분석 결과."""

    department: str = ""

    # 개인정보는 분석 과정에서만 사용하고 결과에는 포함하지 않습니다.
    student_number: Optional[str] = Field(default=None, exclude=True)

    # 입학연도. 학과별 교육과정은 매년 바뀌므로, 학생이 실제로 따라야
    # 하는 교육과정편람(예: "2023 교육과정.pdf")을 고르는 데 사용합니다.
    admission_year: Optional[int] = None

    total_earned_credits: float = 0
    major_credits: float = 0
    general_education_credits: float = 0

    # 교양 세부 구분. general_education_credits는 이 둘의 합입니다.
    general_education_common_credits: float = 0  # 공통교양 (교기+교해+교글+교인 등)
    general_education_advanced_credits: float = 0  # 심화교양 (심교, 학과 지정)

    general_elective_credits: float = 0
    gpa: Optional[float] = None

    # 대부분의 장학금 선발 기준은 누적 평점이 아니라 "직전학기"
    # 성적/취득학점을 기준으로 합니다. 성적표의 가장 최근 학기
    # 요약 정보(학점 계, 평점 평균)에서 추출합니다.
    latest_semester_gpa: Optional[float] = None
    latest_semester_credits: Optional[float] = None

    completed_courses: list[Course] = Field(default_factory=list)

    @field_validator("department", mode="before")
    @classmethod
    def _default_department(cls, value: Any) -> str:
        if value is None:
            return ""

        return str(value)

    @field_validator("admission_year", mode="before")
    @classmethod
    def _parse_admission_year(cls, value: Any) -> Optional[int]:
        parsed = _coerce_optional_float(value)

        return int(parsed) if parsed else None

    @field_validator(
        "total_earned_credits",
        "major_credits",
        "general_education_credits",
        "general_education_common_credits",
        "general_education_advanced_credits",
        "general_elective_credits",
        mode="before",
    )
    @classmethod
    def _parse_required_credit_fields(cls, value: Any) -> float:
        return _coerce_optional_float(value) or 0

    @field_validator("gpa", "latest_semester_gpa", "latest_semester_credits", mode="before")
    @classmethod
    def _parse_gpa(cls, value: Any) -> Optional[float]:
        return _coerce_optional_float(value)


class CurriculumRequirements(BaseModel):
    """교육과정편람에서 검색한 졸업요건."""

    required_total_credits: float = 0
    required_major_credits: float = 0
    required_general_education_credits: float = 0

    # 교양 세부 구분. required_general_education_credits는 이 둘의 합입니다.
    required_general_education_common_credits: float = 0  # 공통교양
    required_general_education_advanced_credits: float = 0  # 심화교양(학과지정)

    # 화면 표시용 원본 텍스트 (예: "72이상", "30~46"). 계산에는
    # 위의 숫자 필드(범위의 최솟값 등)를 사용하고, 이 필드는 사용자에게
    # 편람에 실제로 적힌 표기를 그대로 보여줄 때 사용합니다. 값이
    # 없으면 숫자 필드를 문자열로 표시하면 됩니다.
    required_major_credits_range: str = ""
    required_general_education_credits_range: str = ""
    required_general_education_advanced_credits_range: str = ""

    required_courses: list[str] = Field(default_factory=list)

    # 전공선택 과목 목록 (전공필수는 required_courses에 이미 있음).
    # 다음 학기 추천 과목을 만들 때 "학과 커리큘럼 확인 필요" 같은
    # 애매한 답 대신 실제 과목명을 근거로 추천할 수 있게 합니다.
    elective_courses: list[str] = Field(default_factory=list)

    # RAG에서 검색한 공식 근거
    source_context: str = ""


class GraduationStatus(BaseModel):
    """현재 이수 현황과 졸업요건 비교 결과."""

    current_total_credits: float = 0
    required_total_credits: float = 0
    remaining_total_credits: float = 0

    current_major_credits: float = 0
    required_major_credits: float = 0
    remaining_major_credits: float = 0
    required_major_credits_range: str = ""

    current_general_education_credits: float = 0
    required_general_education_credits: float = 0
    remaining_general_education_credits: float = 0
    required_general_education_credits_range: str = ""

    current_general_education_common_credits: float = 0
    required_general_education_common_credits: float = 0
    remaining_general_education_common_credits: float = 0

    current_general_education_advanced_credits: float = 0
    required_general_education_advanced_credits: float = 0
    remaining_general_education_advanced_credits: float = 0
    required_general_education_advanced_credits_range: str = ""

    missing_required_courses: list[str] = Field(default_factory=list)


class RecommendedCourse(BaseModel):
    """다음 학기 추천 과목."""

    course_name: str
    category: Optional[str] = None
    priority: int = 1
    reason: str = ""


class RecommendedScholarship(BaseModel):
    """추천 장학금 개별 항목."""

    name: str = ""
    possibility: str = "추가 확인 필요"
    reason: str = ""
    requirements_to_check: list[str] = Field(default_factory=list)


class ScholarshipStrategy(BaseModel):
    """현재 성적을 기반으로 한 장학금 검토 방향."""

    current_gpa: Optional[float] = None
    possibility: str = "추가 확인 필요"
    advice: str = ""
    requirements_to_check: list[str] = Field(default_factory=list)
    recommended_scholarships: list[RecommendedScholarship] = Field(
        default_factory=list
    )


class StrategyReport(BaseModel):
    """최종 화면에서 사용하는 전체 분석 결과."""

    student_summary: TranscriptData
    curriculum_requirements: CurriculumRequirements
    graduation_status: GraduationStatus

    recommended_courses: list[RecommendedCourse] = Field(
        default_factory=list
    )

    scholarship_strategy: ScholarshipStrategy = Field(
        default_factory=ScholarshipStrategy
    )

    user_request: Optional[str] = None
    custom_answer: Optional[str] = None