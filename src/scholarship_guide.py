"""
로컬 장학금 안내 PDF를 읽어와 텍스트로 반환합니다.

교육과정편람과 달리 이 문서는 표가 단순하고 페이지 수도 적어서,
표를 컬럼 단위로 파싱하지 않고 전체 텍스트를 그대로 Claude에게
근거 자료로 제공하는 방식을 씁니다.
"""

import logging
from pathlib import Path
from typing import Optional

import pdfplumber

from config.settings import settings

logging.getLogger("pdfminer").setLevel(logging.ERROR)


class ScholarshipGuideNotFoundError(RuntimeError):
    """로컬에서 장학금 안내 PDF를 찾지 못했을 때 발생합니다."""


# 파일명에 이 단어들 중 하나라도 포함되어 있으면 장학금 안내 PDF로 봅니다.
_NAME_HINTS = ("장학", "scholarship")


def get_scholarship_guide_pdf_path() -> Path:
    """
    data/public 폴더에서 장학금 안내 PDF 경로를 반환합니다.
    """

    public_dir = settings.data_dir / "public"
    candidates = [
        path for path in public_dir.glob("*.pdf")
        if any(hint in path.name or hint in path.name.lower() for hint in _NAME_HINTS)
    ]
    if not candidates:
        raise ScholarshipGuideNotFoundError(
            f"{public_dir} 폴더에 장학금 안내 PDF가 없습니다. "
            "파일명에 '장학' 또는 'scholarship'이 포함되어야 합니다."
        )
    return max(candidates, key=lambda path: path.stat().st_mtime)


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
