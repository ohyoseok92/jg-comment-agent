"""① 영상 불러오기 — YouTube Data API v3 + 자막 수집."""
import re
from datetime import datetime, timedelta, timezone

import requests

API = "https://www.googleapis.com/youtube/v3"


def _require(key):
    if not key:
        raise RuntimeError("YOUTUBE_API_KEY가 .env에 없습니다.")


def _get(path: str, params: dict) -> dict:
    r = requests.get(f"{API}/{path}", params=params, timeout=15)
    if r.status_code != 200:
        try:
            msg = r.json()["error"]["message"]
        except Exception:
            msg = r.text[:200]
        raise RuntimeError(f"YouTube API {r.status_code}: {msg}")
    return r.json()


def parse_video_id(url: str):
    """watch?v=, youtu.be/, shorts/, embed/ 형태의 링크에서 영상 ID 추출."""
    if not url:
        return None
    url = url.strip()
    if re.fullmatch(r"[\w-]{11}", url):
        return url
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([\w-]{11})", url)
    return m.group(1) if m else None


def get_videos(key: str, ids: list) -> list:
    _require(key)
    if not ids:
        return []
    data = _get("videos", {"part": "snippet,statistics", "id": ",".join(ids[:50]), "key": key, "hl": "ko"})
    out = []
    for it in data.get("items", []):
        sn, st = it["snippet"], it.get("statistics", {})
        th = sn.get("thumbnails", {})
        thumb = (th.get("medium") or th.get("high") or th.get("default") or {}).get("url", "")
        out.append(
            {
                "id": it["id"],
                "title": sn.get("title", ""),
                "channel": sn.get("channelTitle", ""),
                "published_at": sn.get("publishedAt", ""),
                "description": sn.get("description", ""),
                "thumbnail": thumb,
                "view_count": int(st.get("viewCount", 0)),
                "comment_count": int(st["commentCount"]) if "commentCount" in st else None,
            }
        )
    order = {v: i for i, v in enumerate(ids)}
    out.sort(key=lambda v: order.get(v["id"], 0))
    return out


def search_latest(key: str, keyword: str, max_results: int = 20) -> list:
    _require(key)
    data = _get(
        "search",
        {
            "part": "snippet",
            "q": keyword,
            "type": "video",
            "order": "date",
            "maxResults": max_results,
            "regionCode": "KR",
            "relevanceLanguage": "ko",
            "key": key,
        },
    )
    ids = [it["id"]["videoId"] for it in data.get("items", []) if it["id"].get("videoId")]
    return get_videos(key, ids)


def find_rising(key, keyword, min_views, max_views, min_vph, lookback_hours=48) -> list:
    """가점: 조회수 min~max, 시간당 조회수 min_vph 이상인 '뜨기 직전' 영상."""
    _require(key)
    now = datetime.now(timezone.utc)
    after = (now - timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = _get(
        "search",
        {
            "part": "snippet",
            "q": keyword,
            "type": "video",
            "order": "date",
            "publishedAfter": after,
            "maxResults": 50,
            "regionCode": "KR",
            "relevanceLanguage": "ko",
            "key": key,
        },
    )
    ids = [it["id"]["videoId"] for it in data.get("items", []) if it["id"].get("videoId")]
    result = []
    for v in get_videos(key, ids):
        pub = datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))
        hours = max((now - pub).total_seconds() / 3600, 0.25)
        vph = v["view_count"] / hours
        if min_views <= v["view_count"] <= max_views and vph >= min_vph:
            v["rising"] = True
            v["views_per_hour"] = vph
            result.append(v)
    result.sort(key=lambda v: v["views_per_hour"], reverse=True)
    return result


def get_top_comments(key: str, video_id: str, n: int = 15) -> list:
    _require(key)
    try:
        data = _get(
            "commentThreads",
            {
                "part": "snippet",
                "videoId": video_id,
                "order": "relevance",
                "maxResults": n,
                "textFormat": "plainText",
                "key": key,
            },
        )
    except RuntimeError:
        return []  # 댓글 사용 중지 등
    out = []
    for it in data.get("items", []):
        c = it["snippet"]["topLevelComment"]["snippet"]
        out.append({"text": c.get("textDisplay", ""), "likes": c.get("likeCount", 0)})
    return out


def get_transcript(video_id: str, max_chars: int = 6000) -> str:
    """자막(한국어 우선). 없거나 실패하면 빈 문자열."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return ""
    try:
        api = YouTubeTranscriptApi()
        if hasattr(api, "fetch"):  # youtube-transcript-api 1.x
            fetched = api.fetch(video_id, languages=["ko", "en"])
            text = " ".join(s.text for s in fetched)
        else:  # 0.x
            items = YouTubeTranscriptApi.get_transcript(video_id, languages=["ko", "en"])
            text = " ".join(i["text"] for i in items)
    except Exception:
        return ""
    return re.sub(r"\s+", " ", text).strip()[:max_chars]
