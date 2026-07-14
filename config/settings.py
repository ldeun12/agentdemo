import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    """AWS 없이 실행하는 프로젝트 환경설정."""

    gemini_api_key: str | None
    gemini_model: str
    data_dir: Path

    def validate_gemini(self) -> None:
        if not self.gemini_api_key:
            raise ValueError(
                "GEMINI_API_KEY가 설정되지 않았습니다. 프로젝트 루트의 "
                ".env 파일에 GEMINI_API_KEY=발급받은키 를 입력해주세요."
            )


@lru_cache
def get_settings() -> Settings:
    root = Path(__file__).resolve().parents[1]
    api_key = os.getenv("GEMINI_API_KEY", "").strip() or None

    return Settings(
        gemini_api_key=api_key,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip(),
        data_dir=Path(os.getenv("SCNU_DATA_DIR", root / "data")),
    )


settings = get_settings()
