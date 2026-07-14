import json
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError

from config.settings import settings
from src.curriculum_table_parser import (
    CurriculumPdfNotFoundError,
    parse_curriculum_requirements,
)
from src.schemas import CurriculumRequirements


class CurriculumSearchError(RuntimeError):
    """교육과정편람 검색 중 발생한 오류."""

class CurriculumStructureError(RuntimeError):
    """Claude 교육과정 구조화 과정에서 발생한 오류."""


def build_curriculum_queries(
    department: str,
) -> list[str]:
    """학과명을 바탕으로 교육과정 검색 질의를 생성합니다."""

    cleaned_department = department.strip()

    if not cleaned_department:
        raise ValueError("학과를 입력해주세요.")

    return [
        (
            f"{cleaned_department} 졸업 요건과 "
            "총 졸업학점, 전공학점, 교양학점"
        ),
        (
            f"{cleaned_department} 전공 필수 과목과 "
            "필수 이수 교과목"
        ),
        (
            f"{cleaned_department} 교양 필수 및 "
            "교양 영역별 이수 기준"
        ),
    ]


def get_source_uri(
    retrieval_result: dict[str, Any],
) -> str:
    """Knowledge Base 검색 결과에서 출처 URI를 추출합니다."""

    location = retrieval_result.get("location", {})

    s3_location = location.get("s3Location", {})

    if s3_location.get("uri"):
        return str(s3_location["uri"])

    web_location = location.get("webLocation", {})

    if web_location.get("url"):
        return str(web_location["url"])

    return ""


def is_official_curriculum_source(
    source_uri: str,
) -> bool:
    """검색 결과가 공식 documents 경로인지 확인합니다."""

    expected_prefix = (
        f"s3://{settings.transcript_temp_bucket}/"
        f"{settings.curriculum_s3_prefix}/"
    )

    return source_uri.startswith(expected_prefix)


def retrieve_curriculum_query(
    query: str,
    client=None,
    number_of_results: int = 5,
) -> list[dict[str, Any]]:
    """Knowledge Base에 질의하고 검색 결과를 반환합니다."""

    knowledge_base_id = (
        settings.bedrock_knowledge_base_id
    )

    if not knowledge_base_id:
        raise CurriculumSearchError(
            "BEDROCK_KNOWLEDGE_BASE_ID가 "
            "설정되지 않았습니다."
        )

    if client is None:
        client = boto3.client(
            "bedrock-agent-runtime",
            region_name=settings.aws_region,
        )

    try:
        response = client.retrieve(
            knowledgeBaseId=knowledge_base_id,
            retrievalQuery={
                "text": query,
            },
            retrievalConfiguration={
                "vectorSearchConfiguration": {
                    "numberOfResults": number_of_results,
                }
            },
        )

    except (ClientError, BotoCoreError) as error:
        raise CurriculumSearchError(
            f"교육과정편람 검색 중 오류가 발생했습니다: {error}"
        ) from error

    return response.get("retrievalResults", [])


def search_curriculum_requirements(
    department: str,
    client=None,
    number_of_results: int = 5,
) -> dict[str, Any]:
    """
    학과별 졸업요건을 여러 질의로 검색하고
    중복 결과를 제거하여 하나의 근거로 합칩니다.
    """

    cleaned_department = department.strip()

    queries = build_curriculum_queries(
        cleaned_department
    )

    combined_results: list[dict[str, Any]] = []
    seen_results: set[tuple[str, str]] = set()

    for query in queries:
        retrieval_results = retrieve_curriculum_query(
            query=query,
            client=client,
            number_of_results=number_of_results,
        )

        for result in retrieval_results:
            content = result.get("content", {})
            text = str(content.get("text", "")).strip()
            source_uri = get_source_uri(result)
            score = result.get("score")

            if not text:
                continue

            # 성적증명서 임시 경로 등은 근거로 사용하지 않습니다.
            if not is_official_curriculum_source(
                source_uri
            ):
                continue

            unique_key = (text, source_uri)

            if unique_key in seen_results:
                continue

            seen_results.add(unique_key)

            combined_results.append(
                {
                    "text": text,
                    "source": source_uri,
                    "score": score,
                }
            )

    if not combined_results:
        raise CurriculumSearchError(
            "공식 교육과정편람에서 관련 내용을 "
            "찾지 못했습니다."
        )

    # 검색은 됐지만, 정말 그 학과에 대한 내용인지는
    # 별개로 확인해야 합니다. 문서 텍스트에 학과명이
    # 전혀 언급되지 않으면 다른 학과 문서를 잘못
    # 가져왔을 가능성이 높습니다.
    department_mentioned = any(
        cleaned_department in result["text"]
        for result in combined_results
    )

    print(f"--- 교육과정 RAG 검색 결과 (학과: {cleaned_department}) ---")
    print(f"검색된 근거 수: {len(combined_results)}")
    print(f"학과명이 실제로 언급된 근거가 있는가: {department_mentioned}")

    for result in combined_results:
        print(f"  - source: {result['source']}, score: {result['score']}")

    if not department_mentioned:
        print(
            "⚠️ 경고: 검색된 근거 중 어디에도 "
            f"'{cleaned_department}'가 등장하지 않습니다. "
            "Knowledge Base에 이 학과 편람이 없거나 "
            "학과명 표기가 다를 수 있습니다."
        )

    context_parts = []

    for index, result in enumerate(
        combined_results,
        start=1,
    ):
        context_parts.append(
            f"[근거 {index}]\n"
            f"출처: {result['source']}\n"
            f"내용: {result['text']}"
        )

    return {
        "department": cleaned_department,
        "queries": queries,
        "results": combined_results,
        "context": "\n\n".join(context_parts),
        "sources": sorted(
            {
                result["source"]
                for result in combined_results
            }
        ),
    }

def build_curriculum_prompt(
    search_result: dict[str, Any],
) -> str:
    """검색된 공식 교육과정 내용을 구조화할 프롬프트를 만듭니다."""

    department = search_result.get("department", "")
    context = search_result.get("context", "")

    return f"""
다음은 Bedrock Knowledge Base에서 검색한
순천대학교 공식 교육과정편람 내용입니다.

검색 근거에 명시된 정보만 사용하여 JSON으로 반환하세요.
확인할 수 없는 숫자는 0으로 반환하고 추측하지 마세요.

반드시 다음 JSON 구조만 반환하세요.

{{
  "required_total_credits": 0,
  "required_major_credits": 0,
  "required_general_education_credits": 0,
  "required_courses": []
}}

규칙:
1. 설명이나 Markdown 코드 블록을 작성하지 마세요.
2. 학점은 문자열이 아닌 숫자로 반환하세요.
3. required_courses에는 명시된 필수 과목만 넣으세요.
4. 서로 다른 입학연도의 기준을 임의로 합치지 마세요.

학과: {department}

공식 검색 근거:
{context}
""".strip()

def structure_curriculum_requirements(
    search_result: dict[str, Any],
    client=None,
) -> CurriculumRequirements:
    """Knowledge Base 검색 결과를 Claude로 구조화합니다."""

    if not settings.bedrock_model_id:
        raise CurriculumStructureError(
            "BEDROCK_MODEL_ID가 설정되지 않았습니다."
        )

    source_context = str(
        search_result.get("context", "")
    ).strip()

    if not source_context:
        raise CurriculumStructureError(
            "구조화할 교육과정 검색 근거가 없습니다."
        )

    if client is None:
        client = boto3.client(
            "bedrock-runtime",
            region_name=settings.aws_region,
        )

    prompt = build_curriculum_prompt(search_result)

    try:
        response = client.converse(
            modelId=settings.bedrock_model_id,
            system=[
                {
                    "text": (
                        "당신은 공식 대학 교육과정 문서에서 "
                        "졸업요건만 정확히 추출하는 도우미입니다."
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
        raise CurriculumStructureError(
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
        raise CurriculumStructureError(
            "Claude 응답에서 JSON을 찾지 못했습니다."
        )

    try:
        parsed_data = json.loads(
            response_text[start_index:end_index + 1]
        )

    except json.JSONDecodeError as error:
        raise CurriculumStructureError(
            "Claude가 올바른 JSON을 반환하지 않았습니다."
        ) from error

    parsed_data["source_context"] = source_context

    try:
        return CurriculumRequirements.model_validate(
            parsed_data
        )

    except ValidationError as error:
        raise CurriculumStructureError(
            "Claude 응답이 교육과정 구조와 일치하지 않습니다."
        ) from error
    
def get_curriculum_requirements(
    department: str,
    admission_year: int | None = None,
    retrieval_client=None,
    model_client=None,
) -> CurriculumRequirements:
    """
    교육과정편람 PDF의 표를 직접 파싱해 학과 요건을 찾고,
    (표 구조가 다르거나 학과를 못 찾는 등) 실패한 경우에만
    기존 벡터 검색(RAG) 방식으로 폴백합니다.
    """

    try:
        parsed_result = parse_curriculum_requirements(
            department, admission_year=admission_year
        )

        if parsed_result is not None:
            print(
                f"✅ '{department}' 요건을 PDF 표 직접 파싱으로 찾았습니다. "
                "(RAG 미사용)"
            )
            print(f"  파싱된 값: {parsed_result.model_dump(exclude={'source_context'})}")
            return parsed_result

        print(
            f"⚠️ PDF 표에서 '{department}'를 찾지 못해 RAG 검색으로 폴백합니다."
        )

    except CurriculumPdfNotFoundError as error:
        print(f"⚠️ PDF 직접 파싱을 건너뜁니다: {error}")

    except Exception as error:
        print(
            f"⚠️ PDF 표 직접 파싱 중 예상치 못한 오류가 발생해 RAG로 "
            f"폴백합니다: {type(error).__name__}: {error}"
        )

    search_result = search_curriculum_requirements(
        department=department,
        client=retrieval_client,
    )

    rag_result = structure_curriculum_requirements(
        search_result=search_result,
        client=model_client,
    )

    print(
        "  RAG로 구조화된 값: "
        f"{rag_result.model_dump(exclude={'source_context'})}"
    )

    return rag_result