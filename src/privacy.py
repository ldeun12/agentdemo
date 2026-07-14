import re
import secrets
from pathlib import Path
from typing import Any


TEMP_FOLDER = Path("temp")
MAX_PDF_SIZE = 10 * 1024 * 1024  # 10MB


SENSITIVE_KEYS = {
    "student_name",
    "student_number",
    "student_id",
    "name",
    "email",
    "phone",
    "이름",
    "학번",
    "이메일",
    "전화번호",
}


def ensure_temp_folder(temp_folder: Path = TEMP_FOLDER) -> Path:
    """PDF 임시 저장 폴더가 없으면 생성합니다."""

    temp_folder.mkdir(parents=True, exist_ok=True)
    return temp_folder


def validate_pdf(uploaded_file) -> bytes:
    """업로드 파일이 사용 가능한 PDF인지 검사합니다."""

    if uploaded_file is None:
        raise ValueError("업로드된 파일이 없습니다.")

    file_name = getattr(uploaded_file, "name", "")

    if not file_name.lower().endswith(".pdf"):
        raise ValueError("PDF 파일만 업로드할 수 있습니다.")

    file_data = uploaded_file.getvalue()

    if not file_data:
        raise ValueError("업로드된 PDF가 비어 있습니다.")

    if len(file_data) > MAX_PDF_SIZE:
        raise ValueError("PDF 파일 크기는 10MB 이하여야 합니다.")

    if not file_data.startswith(b"%PDF"):
        raise ValueError("올바른 PDF 파일이 아닙니다.")

    return file_data


def save_uploaded_file(
    uploaded_file,
    temp_folder: Path = TEMP_FOLDER,
) -> str:
    """
    업로드된 PDF를 임의의 안전한 파일명으로 임시 저장합니다.

    사용자가 업로드한 원본 파일명은 저장 파일명으로 사용하지 않습니다.
    """

    file_data = validate_pdf(uploaded_file)
    temp_folder = ensure_temp_folder(temp_folder)

    random_name = f"transcript_{secrets.token_hex(16)}.pdf"
    file_path = temp_folder / random_name

    with file_path.open("wb") as file:
        file.write(file_data)

    return str(file_path.resolve())


def delete_uploaded_file(
    file_path: str | Path | None,
    temp_folder: Path = TEMP_FOLDER,
) -> bool:
    """
    temp 폴더 안에 있는 PDF만 삭제합니다.

    다른 경로에 있는 파일은 삭제하지 않습니다.
    """

    if file_path is None:
        return False

    temp_folder = ensure_temp_folder(temp_folder).resolve()
    target_path = Path(file_path).resolve()

    try:
        target_path.relative_to(temp_folder)
    except ValueError:
        raise ValueError("임시 폴더 밖의 파일은 삭제할 수 없습니다.")

    if not target_path.exists():
        return False

    if not target_path.is_file():
        return False

    target_path.unlink()
    return True


def cleanup_temp_folder(
    temp_folder: Path = TEMP_FOLDER,
) -> int:
    """이전에 남아 있던 임시 PDF를 모두 삭제합니다."""

    temp_folder = ensure_temp_folder(temp_folder)
    deleted_count = 0

    for file_path in temp_folder.iterdir():
        if not file_path.is_file():
            continue

        if file_path.name == ".gitkeep":
            continue

        if file_path.suffix.lower() != ".pdf":
            continue

        file_path.unlink()
        deleted_count += 1

    return deleted_count


def mask_sensitive_text(text: str) -> str:
    """문자열에 포함된 주요 개인정보를 마스킹합니다."""

    if not isinstance(text, str):
        return text

    # 이메일
    text = re.sub(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "[이메일 마스킹]",
        text,
    )

    # 대한민국 휴대전화 번호
    text = re.sub(
        r"\b01[016789][-\s]?\d{3,4}[-\s]?\d{4}\b",
        "[전화번호 마스킹]",
        text,
    )

    # '이름: 홍길동' 형태
    text = re.sub(
        r"(이름\s*[:：]\s*)[가-힣]{2,5}",
        r"\1[이름 마스킹]",
        text,
    )

    # '학번: 20230001' 형태
    text = re.sub(
        r"(학번\s*[:：]\s*)[A-Za-z0-9-]{5,20}",
        r"\1[학번 마스킹]",
        text,
    )

    return text


def sanitize_strategy_report(value: Any) -> Any:
    """
    최종 분석 결과에서 개인정보 필드를 제거하고
    문자열 내부의 개인정보를 마스킹합니다.
    """

    if isinstance(value, dict):
        safe_result = {}

        for key, item in value.items():
            normalized_key = str(key).strip().lower()

            if normalized_key in SENSITIVE_KEYS:
                continue

            safe_result[key] = sanitize_strategy_report(item)

        return safe_result

    if isinstance(value, list):
        return [
            sanitize_strategy_report(item)
            for item in value
        ]

    if isinstance(value, tuple):
        return tuple(
            sanitize_strategy_report(item)
            for item in value
        )

    if isinstance(value, str):
        return mask_sensitive_text(value)

    return value