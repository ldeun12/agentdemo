"""
장학금 안내 PDF(S3)를 읽어와 텍스트로 반환합니다.

교육과정편람과 달리 이 문서는 표가 단순하고 페이지 수도 적어서,
표를 컬럼 단위로 파싱하지 않고 전체 텍스트를 그대로 Claude에게
근거 자료로 제공하는 방식을 씁니다.
"""

import logging
from pathlib import Path
from typing import Optional

import boto3
import pdfplumber
from botocore.exceptions import BotoCoreError, ClientError

from config.settings import settings

logging.getLogger("pdfminer").setLevel(logging.ERROR)


class ScholarshipGuideNotFoundError(RuntimeError):
    """S3에서 장학금 안내 PDF를 찾지 못했을 때 발생합니다."""


_CACHE_DIR = Path("/tmp/scholarship_guide_cache")

# 파일명에 이 단어들 중 하나라도 포함되어 있으면 장학금 안내 PDF로 봅니다.
_NAME_HINTS = ("장학", "scholarship")


def get_scholarship_guide_pdf_path() -> Path:
    """
    S3에 업로드된 장학금 안내 PDF를 로컬 캐시로 내려받고 경로를 반환합니다.
    이미 캐시되어 있으면 재다운로드하지 않습니다.
    """

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    bucket_name = settings.transcript_temp_bucket
    prefix = settings.curriculum_s3_prefix  # 편람과 같은 documents/ 폴더 사용

    s3_client = boto3.client("s3", region_name=settings.aws_region)

    try:
        response = s3_client.list_objects_v2(
            Bucket=bucket_name,
            Prefix=f"{prefix}/",
        )

    except (ClientError, BotoCoreError) as error:
        raise ScholarshipGuideNotFoundError(
            f"S3에서 장학금 안내 PDF 목록을 가져오지 못했습니다: {error}"
        ) from error

    candidates = []

    for item in response.get("Contents", []):
        name = Path(item["Key"]).name

        if not name.lower().endswith(".pdf"):
            continue

        if any(hint in name or hint in name.lower() for hint in _NAME_HINTS):
            candidates.append(item)

    if not candidates:
        raise ScholarshipGuideNotFoundError(
            f"s3://{bucket_name}/{prefix}/ 아래에 장학금 안내 PDF가 없습니다. "
            "파일명에 '장학' 또는 'scholarship'이 포함되어야 합니다."
        )

    target_item = max(candidates, key=lambda item: item["LastModified"])
    target_key = target_item["Key"]

    local_path = _CACHE_DIR / Path(target_key).name

    if not local_path.exists():
        try:
            s3_client.download_file(bucket_name, target_key, str(local_path))

        except (ClientError, BotoCoreError) as error:
            raise ScholarshipGuideNotFoundError(
                f"장학금 안내 PDF 다운로드에 실패했습니다: {error}"
            ) from error

    return local_path


def get_scholarship_guide_text(pdf_path: Optional[Path] = None) -> str:
    """장학금 안내 PDF의 전체 텍스트를 반환합니다."""

    resolved_path = pdf_path or get_scholarship_guide_pdf_path()

    texts = []

    with pdfplumber.open(resolved_path) as pdf:
        for page in pdf.pages:
            text = (page.extract_text() or "").strip()

            if text:
                texts.append(text)

    return "\n\n".join(texts)