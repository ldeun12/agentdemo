from src.gemini_client import generate_json


if __name__ == "__main__":
    result = generate_json('JSON만 반환하세요: {"status":"ok"}')
    print("Gemini 연결 성공:", result)
