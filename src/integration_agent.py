from pathlib import Path

from src.curriculum_rag import get_curriculum_requirements
from src.graduation_analyzer import analyze_graduation_status
from src.schemas import StrategyReport
from src.strategy_agent import generate_strategy_report
from src.transcript_agent import extract_transcript_data


class IntegrationError(RuntimeError):
    """전체 분석 파이프라인에서 발생한 오류."""


def run_full_analysis(
    pdf_path: str | Path,
    department_hint: str | None = None,
    user_request: str | None = None,
) -> StrategyReport:
    """PDF 분석부터 최종 전략 리포트까지 실행합니다."""

    transcript = extract_transcript_data(
        pdf_path=pdf_path,
        department_hint=department_hint,
    )

    department = (
        department_hint.strip()
        if department_hint and department_hint.strip()
        else transcript.department.strip()
    )

    if not department:
        raise IntegrationError(
            "학과를 확인할 수 없습니다. 학과를 직접 선택해주세요."
        )

    transcript = transcript.model_copy(
        update={"department": department}
    )

    requirements = get_curriculum_requirements(
        department=department,
        admission_year=transcript.admission_year,
    )

    graduation_status = analyze_graduation_status(
        transcript=transcript,
        requirements=requirements,
    )

    return generate_strategy_report(
        transcript=transcript,
        requirements=requirements,
        graduation_status=graduation_status,
        user_request=user_request,
    )


def run_full_pipeline(
    pdf_path: str | Path,
    department: str | None = None,
    user_request: str | None = None,
) -> StrategyReport:
    """
    app.py 등 외부 코드가 기대하는 이름/인자에 맞춘 별칭 함수입니다.
    실제 로직은 run_full_analysis와 동일하며, department는
    department_hint로 그대로 전달됩니다 (사용자가 직접 입력한
    학과가 있으면 자동 인식된 학과보다 항상 우선합니다).
    """

    return run_full_analysis(
        pdf_path=pdf_path,
        department_hint=department,
        user_request=user_request,
    )