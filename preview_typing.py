"""댓글 입력까지만 검증한다. 게시 버튼은 호출하지 않는다."""
import argparse
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from poster import SEL
from youtube_api import parse_video_id

ROOT = Path(__file__).parent
SAMPLE = "[광고] 운명원 연애 사주 댓글 입력 테스트입니다. 이 문장은 게시하지 않고 브라우저를 닫습니다."


def preview(video_id: str, profile: str = "유튜브1") -> Path:
    folders = {"유튜브1": "youtube1", "유튜브2": "youtube2", "유튜브3": "youtube3"}
    user_data_dir = ROOT / "chrome_profiles" / folders[profile]
    if not user_data_dir.exists():
        raise RuntimeError(f"{profile} 프로필이 없습니다. open_profile.py로 로그인해 주세요.")
    shot_dir = ROOT / "data" / "screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    shot = shot_dir / f"typing_preview_{datetime.now():%Y%m%d_%H%M%S}.png"

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(user_data_dir), channel="chrome", headless=False, locale="ko-KR",
            viewport=None, args=["--start-maximized", "--lang=ko-KR"],
        )
        page = ctx.new_page()
        try:
            page.goto(f"https://www.youtube.com/watch?v={video_id}&hl=ko",
                      wait_until="domcontentloaded", timeout=45000)
            page.wait_for_selector(SEL["watch"], timeout=30000)
            page.wait_for_timeout(1800)
            page.evaluate("() => { const v = document.querySelector('video'); if (v) v.pause(); }")
            placeholder = page.locator(SEL["placeholder"]).first
            for _ in range(16):
                if page.locator(SEL["placeholder"]).count() and placeholder.is_visible():
                    break
                page.mouse.wheel(0, 650)
                page.wait_for_timeout(700)
            else:
                raise RuntimeError("댓글 입력창을 찾지 못했습니다.")
            placeholder.scroll_into_view_if_needed()
            placeholder.click()
            editor = page.locator(SEL["editor"]).first
            editor.wait_for(state="visible", timeout=10000)
            editor.click()
            for char in SAMPLE:
                page.keyboard.type(char)
                page.wait_for_timeout(70)
            if editor.inner_text().strip() != SAMPLE:
                raise RuntimeError("입력한 문구와 화면의 문구가 일치하지 않습니다.")
            page.screenshot(path=str(shot))
            return shot
        except Exception:
            page.screenshot(path=str(shot))
            raise
        finally:
            ctx.close()  # 입력된 미게시 초안도 함께 닫힌다.


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("video", help="YouTube 영상 URL 또는 영상 ID")
    parser.add_argument("--profile", choices=["유튜브1", "유튜브2", "유튜브3"], default="유튜브1")
    args = parser.parse_args()
    vid = parse_video_id(args.video)
    if not vid:
        parser.error("올바른 YouTube 영상 URL 또는 ID가 아닙니다.")
    screenshot = preview(vid, args.profile)
    print(f"게시 없이 입력 확인 완료: {screenshot}")
