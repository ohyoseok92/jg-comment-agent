"""최초 1회: 프로필 폴더로 일반 크롬을 열어 구글 계정에 직접 로그인한다.
    python open_profile.py 유튜브1
로그인이 끝나면 크롬 창을 완전히 닫아야 대시보드가 같은 프로필을 열 수 있다.
"""
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
CANDIDATES = {
    "Windows": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
    "Darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
    "Linux": ["google-chrome", "google-chrome-stable"],
}


def find_chrome():
    for c in CANDIDATES.get(platform.system(), []):
        if Path(c).exists():
            return c
        found = shutil.which(c)
        if found:
            return found
    return None


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    profiles = {p["name"]: p for p in cfg["profiles"]}
    if len(sys.argv) < 2 or sys.argv[1] not in profiles:
        print("사용법: python open_profile.py <" + "|".join(profiles) + ">")
        return
    chrome = find_chrome()
    if not chrome:
        print("크롬 실행 파일을 찾지 못했습니다. CANDIDATES에 경로를 추가하세요.")
        return
    d = (ROOT / profiles[sys.argv[1]]["user_data_dir"]).resolve()
    d.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([chrome, f"--user-data-dir={d}", "--lang=ko",
                      "https://accounts.google.com/ServiceLogin?continue=https://www.youtube.com/?hl=ko"])
    print(f"[{sys.argv[1]}] 크롬을 열었습니다 → 구글 로그인 후 유튜브 언어가 한국어인지 확인하고, 창을 닫으세요.")


if __name__ == "__main__":
    main()
