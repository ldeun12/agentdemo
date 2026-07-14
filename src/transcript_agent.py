import json
import time
import uuid
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError

from config.settings import settings
from src.schemas import TranscriptData


class TranscriptAnalysisError(RuntimeError):
    """성적증명서 분석 과정에서 발생한 오류."""


def extract_block_text(
    block: dict[str, Any],
    block_map: dict[str, dict[str, Any]],
) -> str:
    """Textract 블록의 자식 단어를 문자열로 변환합니다."""

    words: list[str] = []

    for relationship in block.get("Relationships", []):
        if relationship.get("Type") != "CHILD":
            continue

        for child_id in relationship.get("Ids", []):
            child = block_map.get(child_id, {})
            block_type = child.get("BlockType")

            if block_type == "WORD":
                text = child.get("Text", "")

                if text:
                    words.append(text)

            elif block_type == "SELECTION_ELEMENT":
                if child.get("SelectionStatus") == "SELECTED":
                    words.append("선택됨")

    return " ".join(words).strip()


def extract_lines(
    blocks: list[dict[str, Any]],
) -> list[str]:
    """Textract 결과에서 문장 단위 텍스트를 추출합니다."""

    line_blocks = [
        block
        for block in blocks
        if block.get("BlockType") == "LINE" and block.get("Text")
    ]

    def reading_order(block: dict[str, Any]):
        page = block.get("Page", 1)
        bounding_box = block.get("Geometry", {}).get("BoundingBox", {})

        top = bounding_box.get("Top", 0)
        left = bounding_box.get("Left", 0)

        return page, top, left

    line_blocks.sort(key=reading_order)

    return [str(block["Text"]).strip() for block in line_blocks]


def extract_tables(
    blocks: list[dict[str, Any]],
) -> list[list[list[str]]]:
    """
    Textract TABLE과 CELL 블록을
    행과 열로 구성된 표로 변환합니다.
    """

    block_map = {block["Id"]: block for block in blocks if block.get("Id")}

    tables: list[list[list[str]]] = []

    for table_block in blocks:
        if table_block.get("BlockType") != "TABLE":
            continue

        cells: list[dict[str, Any]] = []

        for relationship in table_block.get(
            "Relationships",
            [],
        ):
            if relationship.get("Type") != "CHILD":
                continue

            for child_id in relationship.get("Ids", []):
                child = block_map.get(child_id)

                if child and child.get("BlockType") == "CELL":
                    cells.append(child)

        if not cells:
            continue

        max_row = max(int(cell.get("RowIndex", 1)) for cell in cells)

        max_column = max(int(cell.get("ColumnIndex", 1)) for cell in cells)

        table = [["" for _ in range(max_column)] for _ in range(max_row)]

        for cell in cells:
            row_index = int(cell.get("RowIndex", 1)) - 1
            column_index = int(cell.get("ColumnIndex", 1)) - 1

            table[row_index][column_index] = extract_block_text(cell, block_map)

        tables.append(table)

    return tables


def count_pages(
    blocks: list[dict[str, Any]],
) -> int:
    """Textract 결과에서 전체 페이지 수를 계산합니다."""

    pages = [int(block.get("Page", 1)) for block in blocks]

    return max(pages, default=0)


def wait_for_textract(
    textract_client,
    job_id: str,
    timeout_seconds: int = 180,
    poll_interval: float = 2.0,
) -> dict[str, Any]:
    """Textract 비동기 분석이 완료될 때까지 기다립니다."""

    started_at = time.monotonic()

    while True:
        response = textract_client.get_document_analysis(
            JobId=job_id,
        )

        status = response.get("JobStatus")

        if status == "SUCCEEDED":
            return response

        if status in {"FAILED", "PARTIAL_SUCCESS"}:
            message = response.get(
                "StatusMessage",
                "Textract 문서 분석에 실패했습니다.",
            )

            raise TranscriptAnalysisError(message)

        if time.monotonic() - started_at > timeout_seconds:
            raise TranscriptAnalysisError("Textract 분석 시간이 초과되었습니다.")

        time.sleep(poll_interval)


def collect_textract_blocks(
    textract_client,
    job_id: str,
    first_response: dict[str, Any],
) -> list[dict[str, Any]]:
    """페이지로 나뉘어 반환되는 Textract 결과를 합칩니다."""

    blocks = list(first_response.get("Blocks", []))
    next_token = first_response.get("NextToken")

    while next_token:
        response = textract_client.get_document_analysis(
            JobId=job_id,
            NextToken=next_token,
        )

        blocks.extend(response.get("Blocks", []))
        next_token = response.get("NextToken")

    return blocks


def build_temporary_s3_key() -> str:
    """개인정보를 포함하지 않는 임시 S3 키를 생성합니다."""

    random_name = f"{uuid.uuid4().hex}.pdf"
    prefix = settings.transcript_temp_prefix

    return f"{prefix}/{random_name}"


def analyze_transcript_pdf(
    pdf_path: str | Path,
) -> dict[str, Any]:
    """
    PDF를 S3에 임시 업로드하고 Textract로 분석합니다.

    분석 성공 여부와 관계없이 S3 객체는 마지막에 삭제합니다.
    """

    settings.validate_transcript_settings()

    local_path = Path(pdf_path).resolve()

    if not local_path.exists():
        raise FileNotFoundError(f"PDF 파일을 찾을 수 없습니다: {local_path}")

    if not local_path.is_file():
        raise ValueError("PDF 경로가 파일이 아닙니다.")

    if local_path.suffix.lower() != ".pdf":
        raise ValueError("PDF 파일만 분석할 수 있습니다.")

    bucket_name = settings.transcript_temp_bucket
    object_key = build_temporary_s3_key()

    s3_client = boto3.client(
        "s3",
        region_name=settings.aws_region,
    )

    textract_client = boto3.client(
        "textract",
        region_name=settings.aws_region,
    )

    uploaded_to_s3 = False

    try:
        s3_client.upload_file(
            str(local_path),
            bucket_name,
            object_key,
            ExtraArgs={
                "ContentType": "application/pdf",
                "ServerSideEncryption": "AES256",
            },
        )

        uploaded_to_s3 = True

        start_response = textract_client.start_document_analysis(
            DocumentLocation={
                "S3Object": {
                    "Bucket": bucket_name,
                    "Name": object_key,
                }
            },
            FeatureTypes=[
                "TABLES",
                "FORMS",
            ],
        )

        job_id = start_response["JobId"]

        first_response = wait_for_textract(
            textract_client=textract_client,
            job_id=job_id,
        )

        blocks = collect_textract_blocks(
            textract_client=textract_client,
            job_id=job_id,
            first_response=first_response,
        )

        lines = extract_lines(blocks)
        tables = extract_tables(blocks)

        print("--- Textract 추출 결과 요약 ---")
        print(f"페이지 수: {count_pages(blocks)}")
        print(f"추출된 줄(line) 수: {len(lines)}")
        print(f"추출된 표(table) 수: {len(tables)}")

        for index, table in enumerate(tables):
            print(f"--- 표 {index + 1} 전체 내용 ({len(table)}행 x {len(table[0]) if table else 0}열) ---")
            for row in table:
                print(f"  {row}")

        print("--------------------------------")

        return {
            "text": "\n".join(lines),
            "lines": lines,
            "tables": tables,
            "page_count": count_pages(blocks),
        }

    except (ClientError, BotoCoreError) as error:
        raise TranscriptAnalysisError(
            f"AWS 문서 분석 중 오류가 발생했습니다: {error}"
        ) from error

    finally:
        if uploaded_to_s3:
            try:
                s3_client.delete_object(
                    Bucket=bucket_name,
                    Key=object_key,
                )

            except (ClientError, BotoCoreError) as error:
                print(
                    "경고: S3 임시 PDF 삭제에 실패했습니다.",
                    error,
                )


class BedrockTranscriptError(RuntimeError):
    """Claude 성적 데이터 구조화 과정의 오류."""


# 프롬프트에 예시로 넣어둔 스키마 설명 문구입니다. 모델이 실제 값 대신
# 이 설명 문구를 그대로 베껴서 반환하는 경우가 있어, 그런 값이 들어오면
# 걸러내기 위한 목록입니다.
_PLACEHOLDER_LEAK_MARKERS = (
    "또는 빈 문자열",
    "또는 null",
    "학과명 또는",
)


def _looks_like_placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return False

    return any(marker in value for marker in _PLACEHOLDER_LEAK_MARKERS)


def _sanitize_placeholder_leakage(parsed_data: dict[str, Any]) -> None:
    """
    Claude가 실제 값 대신 프롬프트의 스키마 설명 문구를
    그대로 반환한 경우, 조용히 넘어가지 않고 경고를 남긴 뒤
    해당 필드를 빈 값으로 되돌립니다.
    """

    for key in ("department", "student_number"):
        value = parsed_data.get(key)

        if _looks_like_placeholder(value):
            print(
                f"⚠️ '{key}' 필드가 프롬프트 설명 문구를 그대로 반환했습니다"
                f" ({value!r}). 빈 값으로 대체합니다. "
                "Bedrock 모델이 지시를 제대로 따르지 못하고 있을 "
                "가능성이 높으니 BEDROCK_MODEL_ID와 Textract 추출 "
                "텍스트 품질을 확인해보세요."
            )
            parsed_data[key] = ""


def parse_json_object(response_text: str) -> dict[str, Any]:
    """Claude 응답에서 JSON 객체만 추출합니다."""

    cleaned_text = response_text.strip()

    if cleaned_text.startswith("```"):
        lines = cleaned_text.splitlines()

        if lines:
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        cleaned_text = "\n".join(lines).strip()

    start_index = cleaned_text.find("{")
    end_index = cleaned_text.rfind("}")

    if start_index == -1 or end_index == -1:
        raise BedrockTranscriptError("Claude 응답에서 JSON 객체를 찾지 못했습니다.")

    json_text = cleaned_text[start_index : end_index + 1]

    try:
        parsed = json.loads(json_text)

    except json.JSONDecodeError as error:
        raise BedrockTranscriptError(
            "Claude가 올바른 JSON을 반환하지 않았습니다."
        ) from error

    if not isinstance(parsed, dict):
        raise BedrockTranscriptError("Claude 응답이 JSON 객체 형식이 아닙니다.")

    return parsed


def build_transcript_prompt(
    textract_result: dict[str, Any],
    department_hint: str | None = None,
) -> str:
    """Textract 결과를 구조화하기 위한 프롬프트를 만듭니다. (레거시, 미사용)"""

    input_data = {
        "department_hint": department_hint,
        "textract_text": textract_result.get("text", ""),
        "textract_tables": textract_result.get(
            "tables",
            [],
        ),
    }

    serialized_input = json.dumps(
        input_data,
        ensure_ascii=False,
    )

    return f"""
다음은 대학 성적증명서를 Amazon Textract로 분석한 결과입니다.

입력에 존재하는 정보만 사용하여 JSON으로 구조화하세요.
확인할 수 없는 값은 추측하지 마세요.

반드시 다음 JSON 구조만 반환하세요.

{{
  "department": "학과명 또는 빈 문자열",
  "student_number": "학번 또는 null",
  "total_earned_credits": 0,
  "major_credits": 0,
  "general_education_credits": 0,
  "general_elective_credits": 0,
  "gpa": null,
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
1. 설명이나 Markdown 코드 블록을 쓰지 마세요.
2. 숫자 필드는 문자열이 아닌 숫자로 반환하세요.s
3. 정보가 없으면 학점은 0, 문자열은 null 또는 빈 문자열로 반환하세요.
4. 과목을 중복해서 넣지 마세요.
5. 총 취득학점을 과목 합계로 임의 계산하지 마세요.
6. department_hint는 학과 확인을 돕는 참고값으로만 사용하세요.
7. "학과", "학부", "전공", "소속" 항목 주변을 확인하여 department를 추출하세요.
8. department에는 단과대학명이 아니라 학생의 실제 학과·학부·전공명을 반환하세요.
9. 표와 본문 중 한쪽에만 학과 정보가 있어도 반드시 확인하세요.

입력:
{serialized_input}
""".strip()


def build_transcript_prompt_direct(
    department_hint: str | None = None,
) -> str:
    """
    Claude가 PDF를 직접 읽고 구조화하도록 지시하는 프롬프트를 만듭니다.

    Amazon Textract는 한국어를 지원하지 않습니다 (공식 지원 언어: 영어,
    스페인어, 이탈리아어, 포르투갈어, 프랑스어, 독일어). 그래서 한글
    성적증명서를 Textract로 OCR하면 한글이 의미 없는 라틴 문자로
    깨집니다. Claude는 PDF/이미지 속 한글을 네이티브로 잘 읽으므로,
    Textract를 거치지 않고 PDF를 직접 첨부해 구조화합니다.
    """

    hint_line = (
        f"학과 힌트(참고용, 확정 근거로 쓰지 말 것): {department_hint}"
        if department_hint
        else "학과 힌트: 없음"
    )

    return f"""
첨부된 PDF는 대학 성적증명서입니다. PDF 안의 내용을 직접 읽고
분석하여 아래 JSON 구조로 반환하세요.

PDF에 실제로 존재하는 정보만 사용하세요. 확인할 수 없는 값은
추측하지 마세요. QR코드, 원본확인 도장, 워터마크 텍스트는
성적 데이터가 아니므로 무시하세요.

매우 중요: 원본확인 도장이나 스티커, 워터마크가 글자 위에
겹쳐져 있어 일부라도 가려진 글자는 절대로 비슷하게 지어내거나
그럴듯한 값으로 채우지 마세요. 특히 이름, 학번, 학과처럼 도장
근처에 있는 필드가 그렇습니다. 가려져서 확신할 수 없다면
반드시 null(또는 department는 빈 문자열)을 반환하세요.
틀린 값을 자신 있게 채우는 것보다 모른다고 하는 것이 훨씬
낫습니다.

반드시 다음 JSON 구조만 반환하세요.

{{
  "department": "학과명 또는 빈 문자열",
  "student_number": "학번 또는 null",
  "admission_year": "입학연도(4자리 숫자) 또는 null",
  "total_earned_credits": 0,
  "major_credits": 0,
  "general_education_credits": 0,
  "general_education_common_credits": 0,
  "general_education_advanced_credits": 0,
  "general_elective_credits": 0,
  "gpa": null,
  "latest_semester_gpa": "가장 최근 학기 평점 평균 또는 null",
  "latest_semester_credits": "가장 최근 학기 취득 학점(학점 계) 또는 null",
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
    "일선"(일반선택), "교기"(교양기초), "교해"(교양핵심),
    "교글"(교양글로벌의사소통), "교인"(교양인성), "심교"(심화교양)
    같은 약어 열로 구성됩니다. 다음과 같이 채우세요:
    - major_credits = "전필" + "전선"의 합
    - general_elective_credits = "일선" 열의 값
    - general_education_common_credits = "교기"+"교해"+"교글"+"교인"
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


def _extract_response_text(response: dict[str, Any]) -> str:
    """Bedrock converse() 응답에서 텍스트 부분만 뽑아냅니다."""

    content_blocks = response.get("output", {}).get("message", {}).get("content", [])

    response_text = "".join(
        str(block.get("text", ""))
        for block in content_blocks
        if isinstance(block, dict)
    ).strip()

    if not response_text:
        stop_reason = response.get("stopReason")
        block_types = [
            list(block.keys()) if isinstance(block, dict) else type(block).__name__
            for block in content_blocks
        ]

        print(f"⚠️ Claude가 빈 응답을 반환했습니다. stopReason={stop_reason!r}")
        print(f"  content 블록 종류: {block_types}")

        if stop_reason == "max_tokens":
            print(
                "  → maxTokens 한도에 도달해 답변이 잘렸습니다. "
                "inferenceConfig의 maxTokens를 더 늘려야 합니다."
            )

        raise BedrockTranscriptError(
            f"Claude가 빈 응답을 반환했습니다. (stopReason={stop_reason})"
        )

    return response_text


def _finalize_transcript_data(response_text: str) -> TranscriptData:
    """Claude 응답 텍스트를 파싱/검증해 TranscriptData로 변환합니다."""

    parsed_data = parse_json_object(response_text)

    print("--- Claude가 추출한 성적표 원본 JSON ---")
    print(json.dumps(parsed_data, ensure_ascii=False, indent=2))

    _sanitize_placeholder_leakage(parsed_data)

    try:
        return TranscriptData.model_validate(parsed_data)

    except ValidationError as error:
        error_details = "; ".join(
            f"{'.'.join(str(loc) for loc in issue['loc'])}: {issue['msg']}"
            for issue in error.errors()
        )
        print("--- 검증 오류 ---")
        print(error_details)

        raise BedrockTranscriptError(
            "Claude 응답이 TranscriptData 구조와 일치하지 않습니다: "
            f"{error_details}"
        ) from error


def structure_transcript_data(
    textract_result: dict[str, Any],
    department_hint: str | None = None,
    client=None,
) -> TranscriptData:
    """Textract 결과를 Claude로 구조화합니다. (레거시, 한국어 문서에는 사용하지 마세요)"""

    if not settings.bedrock_model_id:
        raise BedrockTranscriptError("BEDROCK_MODEL_ID가 설정되지 않았습니다.")

    if client is None:
        client = boto3.client(
            "bedrock-runtime",
            region_name=settings.aws_region,
        )

    prompt = build_transcript_prompt(
        textract_result=textract_result,
        department_hint=department_hint,
    )

    try:
        response = client.converse(
            modelId=settings.bedrock_model_id,
            system=[
                {
                    "text": (
                        "당신은 대학 성적증명서를 "
                        "정확한 JSON으로 변환하는 "
                        "데이터 추출 도우미입니다."
                    )
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": prompt,
                        }
                    ],
                }
            ],
            inferenceConfig={
                "maxTokens": 8192,
            },
        )

    except (ClientError, BotoCoreError) as error:
        raise BedrockTranscriptError(
            f"Claude 호출 중 오류가 발생했습니다: {error}"
        ) from error

    response_text = _extract_response_text(response)

    return _finalize_transcript_data(response_text)


def _render_pdf_pages_as_images(
    pdf_bytes: bytes,
    dpi: int = 300,
    max_pages: int = 5,
) -> list[bytes]:
    """
    PDF 페이지를 고해상도 PNG 이미지로 렌더링합니다.

    Bedrock에 원본 PDF 바이트를 그대로 첨부하면 내부적으로 미리보기
    수준의 해상도로 렌더링될 수 있어, 이 성적증명서처럼 촘촘한 다단
    표에서는 글자가 뭉개져 보일 수 있습니다. 페이지를 직접 300dpi로
    렌더링해 이미지로 보내면 인식 정확도가 올라갑니다.

    PyMuPDF(fitz)가 설치되어 있지 않거나 렌더링에 실패하면 빈 리스트를
    반환하며, 호출부는 이 경우 원본 PDF 첨부 방식으로 폴백합니다.
    """

    try:
        import fitz  # PyMuPDF
    except ImportError as error:
        print(f"⚠️ pymupdf(fitz)를 임포트할 수 없습니다: {error}")
        return []

    try:
        images: list[bytes] = []
        zoom = dpi / 72
        matrix = fitz.Matrix(zoom, zoom)

        with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
            for page in document[:max_pages]:
                pixmap = page.get_pixmap(matrix=matrix)
                images.append(pixmap.tobytes("png"))

        return images

    except Exception as error:
        print(f"⚠️ PDF를 이미지로 렌더링하는 중 오류가 발생했습니다: {error}")
        return []


def _build_transcript_content_blocks(
    pdf_bytes: bytes,
    prompt: str,
) -> list[dict[str, Any]]:
    """
    Claude에게 보낼 content 블록을 만듭니다. 가능하면 페이지를
    고해상도 이미지로 렌더링해서 보내고, 실패하면 원본 PDF를
    문서 블록으로 첨부합니다.
    """

    page_images = _render_pdf_pages_as_images(pdf_bytes)

    if page_images:
        print(
            f"✅ PDF를 고해상도 이미지 {len(page_images)}장으로 렌더링해서 "
            "Claude에게 전달합니다."
        )
        content: list[dict[str, Any]] = [
            {"image": {"format": "png", "source": {"bytes": image_bytes}}}
            for image_bytes in page_images
        ]
        content.append({"text": prompt})

        return content

    print(
        "⚠️ 고해상도 이미지 렌더링에 실패해 원본 PDF 파일을 그대로 "
        "첨부합니다. (pymupdf/fitz가 설치되어 있는지 확인해보세요: "
        "pip install pymupdf)"
    )

    return [
        {
            "document": {
                "format": "pdf",
                "name": "transcript",
                "source": {"bytes": pdf_bytes},
            }
        },
        {"text": prompt},
    ]


def structure_transcript_data_from_pdf(
    pdf_path: str | Path,
    department_hint: str | None = None,
    client=None,
) -> TranscriptData:
    """
    PDF를 Claude에게 직접 첨부해 구조화합니다 (Amazon Textract 미사용).

    Amazon Textract는 한국어를 지원하지 않아 한글 성적증명서를 거치면
    텍스트가 깨집니다. Claude는 PDF/이미지 속 한글을 네이티브로 잘
    읽으므로, 이 경로가 한글 문서에는 훨씬 신뢰할 수 있습니다.
    """

    if not settings.bedrock_model_id:
        raise BedrockTranscriptError("BEDROCK_MODEL_ID가 설정되지 않았습니다.")

    local_path = Path(pdf_path).resolve()

    if not local_path.exists():
        raise FileNotFoundError(f"PDF 파일을 찾을 수 없습니다: {local_path}")

    if local_path.suffix.lower() != ".pdf":
        raise ValueError("PDF 파일만 분석할 수 있습니다.")

    if client is None:
        client = boto3.client(
            "bedrock-runtime",
            region_name=settings.aws_region,
        )

    with open(local_path, "rb") as file:
        pdf_bytes = file.read()

    prompt = build_transcript_prompt_direct(department_hint=department_hint)

    content_blocks = _build_transcript_content_blocks(pdf_bytes, prompt)

    try:
        response = client.converse(
            modelId=settings.bedrock_model_id,
            system=[
                {
                    "text": (
                        "당신은 대학 성적증명서 PDF를 직접 읽고 "
                        "정확한 JSON으로 변환하는 데이터 추출 도우미입니다."
                    )
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": content_blocks,
                }
            ],
            inferenceConfig={
                "maxTokens": 8192,
            },
        )

    except (ClientError, BotoCoreError) as error:
        raise BedrockTranscriptError(
            f"Claude 호출 중 오류가 발생했습니다: {error}"
        ) from error

    response_text = _extract_response_text(response)

    return _finalize_transcript_data(response_text)


def extract_transcript_data(
    pdf_path: str | Path,
    department_hint: str | None = None,
    client=None,
) -> TranscriptData:
    """
    PDF를 Claude에게 직접 보여줘서 구조화합니다.

    이전에는 Amazon Textract로 OCR한 뒤 텍스트를 Claude에게 넘겼지만,
    Textract가 한국어를 지원하지 않아(공식 지원: 영어/스페인어/이탈리아어/
    포르투갈어/프랑스어/독일어) 한글 성적증명서가 깨지는 문제가 있었습니다.
    그래서 Claude가 PDF를 직접 읽는 방식으로 바꿨습니다.
    """

    return structure_transcript_data_from_pdf(
        pdf_path=pdf_path,
        department_hint=department_hint,
        client=client,
    )