"""① 영상 불러오기 — YouTube Data API v3 + 자막 수집."""
import re
from datetime import datetime, timedelta, timezone

import requests

API = "https://www.googleapis.com/youtube/v3"


def _require(key):
    if not key:
        raise RuntimeError("YOUTUBE_API_KEY가 .env에 없습니다.")


def _get(path: str, params: dict) -> dict:
    try:
        r = requests.get(f"{API}/{path}", params=params, timeout=15)
    except requests.RequestException:
        # requests 예외에는 key가 포함된 요청 URL이 들어갈 수 있다.
        raise RuntimeError("YouTube API에 연결하지 못했습니다. 네트워크를 확인하세요.") from None
    if r.status_code != 200:
        try:
            msg = r.json()["error"]["message"]
        except Exception:
            msg = r.text[:200]
        if params.get("key"):
            msg = msg.replace(params["key"], "[API KEY]")
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
        if is_rising(v, min_views, max_views, min_vph, lookback_hours, now):
            v["rising"] = True
            v["views_per_hour"] = views_per_hour(v, now)
            result.append(v)
    result.sort(key=lambda v: v["views_per_hour"], reverse=True)
    return result


def views_per_hour(video: dict, now=None) -> float:
    now = now or datetime.now(timezone.utc)
    pub = datetime.fromisoformat(video["published_at"].replace("Z", "+00:00"))
    return video["view_count"] / max((now - pub).total_seconds() / 3600, 0.25)


def is_rising(video: dict, min_views: int, max_views: int, min_vph: int,
              lookback_hours: int = 48, now=None) -> bool:
    """검색 경로와 무관하게 뜨기 직전 영상의 게시를 막는 공통 조건."""
    now = now or datetime.now(timezone.utc)
    pub = datetime.fromisoformat(video["published_at"].replace("Z", "+00:00"))
    age_hours = (now - pub).total_seconds() / 3600
    return (0 <= age_hours <= lookback_hours
            and min_views <= video["view_count"] <= max_views
            and views_per_hour(video, now) >= min_vph)


def is_rising_now(key: str, video_id: str, min_views: int, max_views: int,
                  min_vph: int, lookback_hours: int = 48) -> bool:
    """게시 직전 videos.list로 조회수를 다시 받아 뜨기 직전 여부를 판정한다."""
    videos = get_videos(key, [video_id])
    if not videos:
        raise RuntimeError("영상 정보를 다시 확인하지 못했습니다. 게시를 중단합니다.")
    return is_rising(videos[0], min_views, max_views, min_vph, lookback_hours)


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
