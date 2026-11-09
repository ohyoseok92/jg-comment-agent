"""③ 댓글 달기 — 크롬 프로필을 열고, 한 글자씩 타이핑해 게시한 뒤, 내 댓글이 보이는지 확인한다.

대시보드가 별도 프로세스로 실행한다 (Streamlit 이벤트 루프와 분리):
    python poster.py --job-id 3 --user-data-dir ./chrome_profiles/youtube1

주의: 유튜브 DOM 셀렉터는 수시로 바뀔 수 있으니, 실패하면 캡처를 보고 SEL을 고친다.
"""
import argparse
import random
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

import db

ROOT = Path(__file__).parent
SHOT_DIR = ROOT / "data" / "screenshots"

SEL = {
    "watch": "ytd-watch-flexy",
    "signin": "a[href*='accounts.google.com/ServiceLogin']",
    "placeholder": "#simplebox-placeholder",
    "editor": "#contenteditable-root",
    "submit": "ytd-commentbox #submit-button button",
    "thread": "ytd-comment-thread-renderer",
    "content": "#content-text",
}


class PostError(Exception):
    pass


def run(job_id: int, user_data_dir: str, min_delay: int, max_delay: int, hold: int):
    job = db.get_job(job_id)
    if not job or job["status"] != "queued":
        return
    text, vid = job["text"], job["video_id"]
    db.update_status(job_id, "opening", "프로필 여는 중")

    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir,
                channel="chrome",  # 설치된 크롬 사용
                headless=False,  # 화면에 보이게
                locale="ko-KR",
                viewport=None,
                args=["--start-maximized", "--lang=ko-KR"],
            )
        except Exception as e:
            db.update_status(job_id, "failed", f"크롬 프로필을 열지 못했습니다(같은 프로필 창이 열려 있으면 닫아 주세요): {e}")
            return

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        submitted = False
        try:
            page.goto(f"https://www.youtube.com/watch?v={vid}&hl=ko", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_selector(SEL["watch"], timeout=30000)
            page.wait_for_timeout(2500)
            page.evaluate("() => { const v = document.querySelector('video'); if (v) v.pause(); }")

            if page.locator(SEL["signin"]).count() > 0:
                raise PostError("이 프로필은 유튜브에 로그인되어 있지 않습니다. open_profile.py로 먼저 로그인하세요.")

            # 댓글 영역이 로드될 때까지 스크롤
            ph = page.locator(SEL["placeholder"]).first
            for _ in range(15):
                if page.locator(SEL["placeholder"]).count() and ph.is_visible():
                    break
                if page.get_by_text("댓글이 사용 중지").count():
                    raise PostError("댓글이 사용 중지된 영상입니다.")
                page.mouse.wheel(0, 700)
                page.wait_for_timeout(900)
            else:
                raise PostError("댓글 입력창을 찾지 못했습니다.")

            ph.scroll_into_view_if_needed()
            ph.click()
            editor = page.locator(SEL["editor"]).first
            editor.wait_for(state="visible", timeout=10000)
            editor.click()

            db.update_status(job_id, "typing", "입력 중")
            for ch in text:  # 한 글자씩 타이핑
                page.keyboard.type(ch)
                page.wait_for_timeout(random.randint(min_delay, max_delay))
            page.wait_for_timeout(800)

            submit = page.locator(SEL["submit"]).first
            submit.wait_for(state="visible", timeout=5000)
            # 클릭 직전 DB에 기록한다. 이 사이 프로세스가 종료되어도 자동 재시도로 중복 게시하지 않는다.
            db.update_status(job_id, "submitting", "게시 버튼을 누르고 확인 중")
            submitted = True
            submit.click()

            # 내 댓글이 목록에 보이는지 확인
            snippet = text.strip()[-20:]
            mine = page.locator(SEL["thread"]).filter(
                has=page.locator(SEL["content"], has_text=snippet)
            ).first
            mine.wait_for(state="visible", timeout=20000)
            mine.scroll_into_view_if_needed()
            rendered = mine.locator(SEL["content"]).first.inner_text().strip()
            if rendered != text.strip():
                raise PostError("댓글 목록의 문구가 게시한 초안과 일치하지 않습니다.")
            mine.evaluate("el => { el.style.outline = '3px solid #22c55e'; el.style.borderRadius = '8px'; }")

            link = mine.locator("a[href*='lc=']")
            href = link.first.get_attribute("href") if link.count() else None
            if href and href.startswith("/"):
                href = "https://www.youtube.com" + href
            db.update_status(
                job_id, "posted", "게시 완료 · 내 댓글 확인됨",
                comment_url=href or f"https://www.youtube.com/watch?v={vid}",
            )
            page.wait_for_timeout(hold * 1000)

        except Exception as e:
            SHOT_DIR.mkdir(parents=True, exist_ok=True)
            shot = SHOT_DIR / f"job{job_id}_{datetime.now():%Y%m%d_%H%M%S}.png"
            try:
                page.screenshot(path=str(shot))
                shot_path = str(shot)
            except Exception:
                shot_path = None
            reason = str(e).splitlines()[0][:300] if str(e) else type(e).__name__
            # 게시 버튼을 이미 눌렀다면 실제로 달렸을 수 있으므로 재시도(중복 게시)를 막는다
            status = "unverified" if submitted else "failed"
            db.update_status(job_id, status, reason, screenshot=shot_path)
        finally:
            ctx.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-id", type=int, required=True)
    ap.add_argument("--user-data-dir", required=True)
    ap.add_argument("--min-delay", type=int, default=60)
    ap.add_argument("--max-delay", type=int, default=170)
    ap.add_argument("--hold", type=int, default=6)
    a = ap.parse_args()
    db.init()
    run(a.job_id, a.user_data_dir, a.min_delay, a.max_delay, a.hold)
