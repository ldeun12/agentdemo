import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


load_dotenv()


def clean_optional(value: str | None) -> str | None:
    """빈 환경변수는 None으로 변환합니다."""

    if value is None:
        return None

    cleaned = value.strip()

    if not cleaned:
        return None

    return cleaned


@dataclass(frozen=True)
class Settings:
    """프로젝트 환경설정."""

    aws_region: str

    transcript_temp_bucket: str
    transcript_temp_prefix: str

    curriculum_s3_prefix: str

    bedrock_knowledge_base_id: str | None
    bedrock_model_id: str | None
    bedrock_guardrail_id: str | None
    bedrock_guardrail_version: str | None

    @property
    def transcript_s3_uri(self) -> str:
        """성적증명서 임시 업로드 경로를 반환합니다."""

        return (
            f"s3://{self.transcript_temp_bucket}/"
            f"{self.transcript_temp_prefix}/"
        )

    def validate_transcript_settings(self) -> None:
        """Textract 분석에 필요한 설정을 검사합니다."""

        if not self.aws_region:
            raise ValueError(
                "AWS_REGION 환경변수가 설정되지 않았습니다."
            )

        if not self.transcript_temp_bucket:
            raise ValueError(
                "TRANSCRIPT_TEMP_BUCKET 환경변수가 "
                "설정되지 않았습니다."
            )

        if not self.transcript_temp_prefix:
            raise ValueError(
                "TRANSCRIPT_TEMP_PREFIX 환경변수가 "
                "설정되지 않았습니다."
            )


@lru_cache
def get_settings() -> Settings:
    """환경변수를 읽어 Settings 객체를 생성합니다."""

    return Settings(
        aws_region=os.getenv(
            "AWS_REGION",
            "ap-northeast-2",
        ).strip(),
        transcript_temp_bucket=os.getenv(
            "TRANSCRIPT_TEMP_BUCKET",
            "",
        ).strip(),
        transcript_temp_prefix=os.getenv(
            "TRANSCRIPT_TEMP_PREFIX",
            "temp-transcripts",
        ).strip("/ "),
        curriculum_s3_prefix=os.getenv(
            "CURRICULUM_S3_PREFIX",
            "documents",
        ).strip("/ "),
        bedrock_knowledge_base_id=clean_optional(
            os.getenv("BEDROCK_KNOWLEDGE_BASE_ID")
        ),
        bedrock_model_id=clean_optional(
            os.getenv("BEDROCK_MODEL_ID")
        ),
        bedrock_guardrail_id=clean_optional(
            os.getenv("BEDROCK_GUARDRAIL_ID")
        ),
        bedrock_guardrail_version=clean_optional(
            os.getenv("BEDROCK_GUARDRAIL_VERSION")
        ),
    )


settings = get_settings()