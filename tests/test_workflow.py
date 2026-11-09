from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import db
import drafter
import youtube_api as yt


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "agent.db")
    db.init()


def test_reservation_limits_and_unknown_post_state(isolated_db, monkeypatch):
    first = db.reserve("abcdefghijk", "첫 영상", "유튜브1", "[광고] 댓글", 1)
    with pytest.raises(db.RuleViolation, match="영상당 1개"):
        db.reserve("abcdefghijk", "첫 영상", "유튜브2", "[광고] 댓글", 1)
    with pytest.raises(db.RuleViolation, match="상한"):
        db.reserve("lmnopqrstuv", "둘째 영상", "유튜브1", "[광고] 댓글", 1)
    db.update_status(first, "submitting")
    with db.connect() as con:
        con.execute("UPDATE comments SET updated_at=? WHERE id=?", ((datetime.now() - timedelta(minutes=20)).isoformat(), first))
    db._fail_stale(minutes=10)
    assert db.get_job(first)["status"] == "unverified"
    with pytest.raises(db.RuleViolation, match="영상당 1개"):
        db.reserve("abcdefghijk", "첫 영상", "유튜브3", "[광고] 댓글", 10)


def test_failed_before_submission_can_retry(isolated_db):
    first = db.reserve("abcdefghijk", "영상", "유튜브1", "[광고] 댓글", 1)
    db.update_status(first, "failed")
    assert db.count_today() == 0
    assert db.reserve("abcdefghijk", "영상", "유튜브1", "[광고] 댓글", 1) > first


def test_rising_filter_uses_age_and_views(monkeypatch):
    now = datetime.now(timezone.utc)
    videos = [
        {"id": "abcdefghijk", "view_count": 3000, "published_at": (now - timedelta(hours=10)).isoformat()},
        {"id": "lmnopqrstuv", "view_count": 500, "published_at": (now - timedelta(hours=1)).isoformat()},
        {"id": "wxyzABCDE12", "view_count": 2000, "published_at": (now - timedelta(hours=20)).isoformat()},
    ]
    monkeypatch.setattr(yt, "_get", lambda *_: {"items": [{"id": {"videoId": v["id"]}} for v in videos]})
    monkeypatch.setattr(yt, "get_videos", lambda *_: videos)
    result = yt.find_rising("test-key", "연애", 1000, 5000, 150)
    assert [v["id"] for v in result] == ["abcdefghijk"]


def test_draft_checks_source_and_disclosure(monkeypatch):
    cfg = {"comment": {"min_chars": 40, "max_chars": 120, "brand": "운명원", "disclosure": "[광고]"}}
    video = {"id": "abcdefghijk", "title": "이별 뒤 연락을 기다려야 할까", "channel": "테스트", "description": "이별 뒤 연락", "thumbnail": ""}
    replies = iter([
        '{"concern":"이별 뒤 연락을 기다릴지","detail":"이별 뒤 연락"}',
        '{"body":"이별 뒤 연락을 기다리는 마음이 참 어렵죠. 운명원에서 연애 사주를 살펴보는 방법도 있습니다."}',
    ])

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        @property
        def messages(self):
            return self

        def create(self, **kwargs):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=next(replies))])

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(drafter.anthropic, "Anthropic", FakeClient)
    monkeypatch.setattr(yt, "get_transcript", lambda *_: "")
    monkeypatch.setattr(yt, "get_top_comments", lambda *_: [])
    draft = drafter.make_draft(video, cfg)
    assert draft["source"] == "제목·설명"
    assert draft["detail"] == "이별 뒤 연락"
    assert draft["comment"].startswith("[광고] ")
    assert drafter.validate(draft["comment"], cfg) == []


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abcdefghijk",
    "https://youtu.be/abcdefghijk",
    "https://www.youtube.com/shorts/abcdefghijk",
    "abcdefghijk",
])
def test_video_id_links(url):
    assert yt.parse_video_id(url) == "abcdefghijk"
