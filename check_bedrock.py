"""
Bedrock 연결과 모델 응답을 확인하는 간단한 스크립트입니다.
scnu-ai 프로젝트 루트에서 이렇게 실행하세요:

    python check_bedrock.py
"""

import boto3

from config.settings import settings


def main() -> None:
    print(f"AWS_REGION: {settings.aws_region}")
    print(f"BEDROCK_MODEL_ID: {settings.bedrock_model_id}")

    if not settings.bedrock_model_id:
        raise SystemExit("BEDROCK_MODEL_ID가 .env에 설정되지 않았습니다.")

    client = boto3.client(
        "bedrock-runtime",
        region_name=settings.aws_region,
    )

    response = client.converse(
        modelId=settings.bedrock_model_id,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "text": (
                            "다음 JSON 구조만 그대로 반환하되, "
                            'name 값에는 실제로 "테스트 성공"이라는 '
                            "문자열을 채워 넣으세요. 설명이나 코드블록 "
                            "없이 JSON만 반환하세요.\n\n"
                            '{"name": "여기에 값을 채우세요"}'
                        )
                    }
                ],
            }
        ],
        inferenceConfig={
            "maxTokens": 100,
        },
    )

    response_text = response["output"]["message"]["content"][0]["text"]

    print("--- 모델 원본 응답 ---")
    print(response_text)
    print("----------------------")

    if "여기에 값을 채우세요" in response_text:
        print(
            "⚠️ 모델이 placeholder 문구를 그대로 베껴서 반환했습니다. "
            "지시를 따르지 못하고 있으므로 BEDROCK_MODEL_ID가 올바른 "
            "Claude 모델인지 확인이 필요합니다."
        )
    elif "테스트 성공" in response_text:
        print("✅ 모델이 지시를 정상적으로 따랐습니다.")
    else:
        print("❓ 예상과 다른 응답입니다. 위 원본 응답을 확인해주세요.")


if __name__ == "__main__":
    main()