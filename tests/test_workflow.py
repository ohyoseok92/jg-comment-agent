from datetime import datetime, timedelta, timezone

import pytest
import requests

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


def test_unverified_survives_new_session_and_can_be_resolved(isolated_db):
    first = db.reserve("abcdefghijk", "영상", "유튜브1", "[광고] 댓글", 10)
    db.update_status(first, "unverified")
    assert [job["id"] for job in db.list_unverified()] == [first]
    db.resolve_unverified(first, posted=False)
    assert db.list_unverified() == []
    assert db.reserve("abcdefghijk", "영상", "유튜브1", "[광고] 댓글", 10) > first


def test_network_error_never_exposes_youtube_api_key(monkeypatch):
    key = "sensitive-test-key"
    def fail(*args, **kwargs):
        raise requests.ConnectionError(f"url=/youtube/v3/search?key={key}")
    monkeypatch.setattr(requests, "get", fail)
    with pytest.raises(RuntimeError) as exc:
        yt.search_latest(key, "연애", 1)
    assert key not in str(exc.value)


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


def test_rising_post_guard_uses_video_metadata_even_after_direct_link():
    now = datetime.now(timezone.utc)
    video = {"view_count": 3000, "published_at": (now - timedelta(hours=10)).isoformat()}
    assert yt.is_rising(video, 1000, 5000, 150, 48, now)
    assert not yt.is_rising({**video, "published_at": (now - timedelta(hours=49)).isoformat()},
                            1000, 5000, 150, 48, now)
    assert not yt.is_rising({**video, "view_count": 999}, 1000, 5000, 150, 48, now)


def test_draft_checks_source_and_disclosure(monkeypatch):
    cfg = {"comment": {"min_chars": 40, "max_chars": 120, "brand": "운명원", "disclosure": "[광고]"}}
    video = {"id": "abcdefghijk", "title": "이별 뒤 연락을 기다려야 할까", "channel": "테스트", "description": "이별 뒤 연락", "thumbnail": ""}
    replies = iter([
        '{"concern":"이별 뒤 연락을 기다릴지","detail":"이별 뒤 연락"}',
        '{"body":"이별 뒤 연락을 기다리는 마음이 참 어렵죠. 운명원에서 연애 사주를 살펴보는 방법도 있습니다."}',
    ])

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr(drafter, "local_model_status", lambda *_: (True, "ready"))
    monkeypatch.setattr(drafter, "_chat", lambda *_: next(replies))
    monkeypatch.setattr(yt, "get_transcript", lambda *_, **__: "")
    monkeypatch.setattr(yt, "get_top_comments", lambda *_, **__: [])
    draft = drafter.make_draft(video, cfg)
    assert draft["source"] == "제목·설명"
    assert draft["detail"] == "이별 뒤 연락"
    assert draft["comment"].startswith("[광고] ")
    assert drafter.validate(draft["comment"], cfg) == []


def test_draft_retries_invalid_analysis_json(monkeypatch):
    cfg = {"comment": {"min_chars": 40, "max_chars": 120, "brand": "운명원", "disclosure": "[광고]"}}
    video = {"id": "abcdefghijk", "title": "이별 뒤 연락을 기다려야 할까", "channel": "테스트", "description": "이별 뒤 연락", "thumbnail": ""}
    replies = iter([
        "not-json",
        '{"concern":"이별 뒤 연락을 기다릴지","detail":"이별 뒤 연락"}',
        '{"body":"이별 뒤 연락을 기다리는 마음이 참 어렵죠. 운명원에서 연애 사주를 살펴보는 방법도 있습니다."}',
    ])
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr(drafter, "local_model_status", lambda *_: (True, "ready"))
    monkeypatch.setattr(drafter, "_chat", lambda *_: next(replies))
    monkeypatch.setattr(yt, "get_transcript", lambda *_, **__: "")
    monkeypatch.setattr(yt, "get_top_comments", lambda *_, **__: [])
    draft = drafter.make_draft(video, cfg)
    assert draft["errors"] == []
    assert draft["detail"] == "이별 뒤 연락"


def test_rejects_brand_as_video_author():
    cfg = {"comment": {"min_chars": 40, "max_chars": 120, "brand": "운명원", "disclosure": "[광고]"}}
    text = "[광고] 헤어진 뒤 연락을 기다리는 고민이네요. 운명원이 알려주는 연애 사주 이론을 참고해 보세요."
    assert any("근거 없는" in error for error in drafter.validate(text, cfg))


def test_matchmaking_word_is_not_mistaken_for_fake_review():
    cfg = {"comment": {"min_chars": 40, "max_chars": 120, "brand": "운명원", "disclosure": "[광고]"}}
    text = "[광고] 이별 뒤 연락을 기다리는 마음이 어렵죠. 운명원 연애 사주로 두 사람 궁합을 맞춰 보는 방법도 있어요."
    assert drafter.validate(text, cfg) == []


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abcdefghijk",
    "https://youtu.be/abcdefghijk",
    "https://www.youtube.com/shorts/abcdefghijk",
    "abcdefghijk",
])
def test_video_id_links(url):
    assert yt.parse_video_id(url) == "abcdefghijk"
