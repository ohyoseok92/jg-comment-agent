"""비용이 드는 호출이나 댓글 게시 없이 로컬 실행 준비를 점검한다."""
import os
from pathlib import Path

from dotenv import load_dotenv

import drafter
from open_profile import find_chrome

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")


def main():
    key_ready = bool(os.getenv("YOUTUBE_API_KEY"))
    print(f"YouTube Data API 키: {'입력됨' if key_ready else '미입력'}")
    model = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b-instruct-q4_K_M")
    ready, message = drafter.local_model_status(model)
    print(f"로컬 AI: {message}")
    chrome = find_chrome()
    print(f"Chrome: {chrome or '설치 경로를 찾지 못함'}")
    profile = ROOT / "chrome_profiles" / "youtube1"
    print(f"유튜브1 프로필 폴더: {'있음(로그인 상태는 직접 확인)' if profile.exists() else '없음'}")
    if key_ready and ready and chrome:
        print("기본 준비 완료. 대시보드에서 영상 검색과 초안 생성을 시험할 수 있습니다.")
    else:
        print("미준비 항목을 설정한 뒤 다시 실행하세요.")


if __name__ == "__main__":
    main()
