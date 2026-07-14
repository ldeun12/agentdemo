import streamlit as st

from src.schemas import StrategyReport


def show_result(
    report: StrategyReport | dict,
) -> None:
    """최종 전략 리포트를 Streamlit 화면에 표시합니다."""

    if isinstance(report, dict):
        report = StrategyReport.model_validate(report)

    status = report.graduation_status
    scholarship = report.scholarship_strategy

    st.subheader("분석 결과")

    col1, col2, col3 = st.columns(3)

    col1.metric(
        "부족한 총 학점",
        f"{status.remaining_total_credits:g}학점",
    )
    col2.metric(
        "부족한 전공 학점",
        f"{status.remaining_major_credits:g}학점",
    )
    col3.metric(
        "부족한 교양 학점",
        f"{status.remaining_general_education_credits:g}학점",
    )

    st.markdown("#### 미이수 필수 과목")

    if status.missing_required_courses:
        for course_name in status.missing_required_courses:
            st.write(f"- {course_name}")
    else:
        st.success("확인된 미이수 필수 과목이 없습니다.")

    st.markdown("#### 다음 학기 추천 과목")

    if report.recommended_courses:
        for course in sorted(
            report.recommended_courses,
            key=lambda item: item.priority,
        ):
            st.write(
                f"**{course.priority}. {course.course_name}**"
                f" — {course.reason}"
            )
    else:
        st.info("추천 과목을 생성하지 못했습니다.")

    st.markdown("#### 장학금 준비 전략")

    if scholarship.current_gpa is not None:
        st.write(f"현재 평점: **{scholarship.current_gpa:g}**")

    st.write(f"검토 결과: **{scholarship.possibility}**")
    st.write(scholarship.advice)

    if scholarship.requirements_to_check:
        st.write("추가 확인 사항")

        for requirement in scholarship.requirements_to_check:
            st.write(f"- {requirement}")

    if report.custom_answer:
        st.markdown("#### 추가 요청 답변")
        st.write(report.custom_answer)

    with st.expander("공식 교육과정 검색 근거"):
        st.text(
            report.curriculum_requirements.source_context
        )