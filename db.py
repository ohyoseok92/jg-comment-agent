"""SQLite 기록 + 운영 규칙(영상당 댓글 1개, 하루 게시 상한)을 코드에서 강제한다."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "agent.db"

# 이 상태들은 '그 영상에 댓글이 달렸거나 달리는 중'으로 본다.
# unverified = 게시 버튼은 눌렀지만 확인 실패 → 중복 게시 방지를 위해 활성으로 취급
ACTIVE = ("queued", "opening", "typing", "submitting", "unverified", "posted")
_Q = ",".join("?" * len(ACTIVE))
_ACTIVE_SQL = ",".join(f"'{s}'" for s in ACTIVE)

STATUS_KO = {
    "queued": "대기",
    "opening": "프로필 여는 중",
    "typing": "입력 중",
    "submitting": "게시 확인 중",
    "unverified": "게시 확인 실패(수동 확인 필요)",
    "posted": "게시 완료",
    "failed": "실패",
    "blocked": "규칙으로 차단",
}


class RuleViolation(Exception):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


def init():
    with connect() as con:
        con.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS comments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id    TEXT NOT NULL,
                video_title TEXT,
                profile     TEXT,
                text        TEXT,
                status      TEXT NOT NULL,
                detail      TEXT,
                screenshot  TEXT,
                comment_url TEXT,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );
            """
        )
        old = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_one_per_video'"
        ).fetchone()
        if old and "submitting" not in old[0]:
            con.execute("DROP INDEX uq_one_per_video")
        con.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS uq_one_per_video "
            f"ON comments(video_id) WHERE status IN ({_ACTIVE_SQL})"
        )
    _fail_stale()


def _fail_stale(minutes: int = 10):
    """게시 클릭이 시작된 작업은 중복 방지를 위해 확인 필요 상태로 남긴다."""
    cutoff = (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    with connect() as con:
        con.execute(
            "UPDATE comments SET status='failed', detail='작업이 중단됨(시간 초과)', updated_at=? "
            "WHERE status IN ('queued','opening','typing') AND updated_at < ?",
            (_now(), cutoff),
        )
        con.execute(
            "UPDATE comments SET status='unverified', detail='게시 도중 작업이 중단됨. 영상에서 댓글을 직접 확인하세요.', updated_at=? "
            "WHERE status='submitting' AND updated_at < ?",
            (_now(), cutoff),
        )


def reserve(video_id: str, video_title: str, profile: str, text: str, daily_max: int) -> int:
    """규칙 검사 후 게시 작업을 등록하고 job id를 돌려준다. 위반 시 RuleViolation."""
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            row = con.execute(
                f"SELECT profile, status FROM comments WHERE video_id=? AND status IN ({_Q})",
                (video_id, *ACTIVE),
            ).fetchone()
            if row:
                raise RuleViolation(
                    f"이 영상에는 이미 {row['profile']} 계정의 댓글이 있습니다 "
                    f"({STATUS_KO[row['status']]}). 영상당 1개만 허용됩니다."
                )
            n = con.execute(
                f"SELECT COUNT(*) FROM comments WHERE created_at LIKE ? AND status IN ({_Q})",
                (_today() + "%", *ACTIVE),
            ).fetchone()[0]
            if n >= daily_max:
                raise RuleViolation(f"오늘 게시 상한({daily_max}개)에 도달했습니다.")
            now = _now()
            cur = con.execute(
                "INSERT INTO comments (video_id, video_title, profile, text, status, created_at, updated_at) "
                "VALUES (?,?,?,?, 'queued', ?, ?)",
                (video_id, video_title, profile, text, now, now),
            )
            con.execute("COMMIT")
            return cur.lastrowid
        except RuleViolation as e:
            con.execute("ROLLBACK")
            now = _now()
            con.execute(
                "INSERT INTO comments (video_id, video_title, profile, text, status, detail, created_at, updated_at) "
                "VALUES (?,?,?,?, 'blocked', ?, ?, ?)",
                (video_id, video_title, profile, text, str(e), now, now),
            )
            raise
        except Exception:
            con.execute("ROLLBACK")
            raise


def update_status(job_id: int, status: str, detail=None, screenshot=None, comment_url=None):
    with connect() as con:
        con.execute(
            "UPDATE comments SET status=?, detail=COALESCE(?, detail), screenshot=COALESCE(?, screenshot), "
            "comment_url=COALESCE(?, comment_url), updated_at=? WHERE id=?",
            (status, detail, screenshot, comment_url, _now(), job_id),
        )


def resolve_unverified(job_id: int, posted: bool):
    """사용자가 실제 영상의 댓글 목록을 확인한 뒤에만 불확정 상태를 해제한다."""
    status = "posted" if posted else "failed"
    detail = "영상에서 댓글 게시를 직접 확인함" if posted else "영상에서 댓글이 없음을 직접 확인함"
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute(
            "UPDATE comments SET status=?, detail=?, updated_at=? WHERE id=? AND status='unverified'",
            (status, detail, _now(), job_id),
        )
        con.execute("COMMIT")
        if cur.rowcount != 1:
            raise RuleViolation("수동 확인이 필요한 작업이 아닙니다.")


def get_job(job_id: int):
    with connect() as con:
        r = con.execute("SELECT * FROM comments WHERE id=?", (job_id,)).fetchone()
        return dict(r) if r else None


def count_today() -> int:
    with connect() as con:
        return con.execute(
            f"SELECT COUNT(*) FROM comments WHERE created_at LIKE ? AND status IN ({_Q})",
            (_today() + "%", *ACTIVE),
        ).fetchone()[0]


def commented_video_ids() -> set:
    with connect() as con:
        rows = con.execute(f"SELECT video_id FROM comments WHERE status IN ({_Q})", ACTIVE).fetchall()
        return {r[0] for r in rows}


def previous_texts(limit: int = 30) -> list:
    with connect() as con:
        rows = con.execute(
            f"SELECT text FROM comments WHERE status IN ({_Q}) ORDER BY id DESC LIMIT ?",
            (*ACTIVE, limit),
        ).fetchall()
        return [r[0] for r in rows if r[0]]


def next_profile(names: list) -> str:
    """영상마다 돌아가며 자동 배정: 마지막으로 쓴 프로필의 다음 것."""
    with connect() as con:
        r = con.execute(
            f"SELECT profile FROM comments WHERE status IN ({_Q}) ORDER BY id DESC LIMIT 1", ACTIVE
        ).fetchone()
    if not r or r[0] not in names:
        return names[0]
    return names[(names.index(r[0]) + 1) % len(names)]


def list_log(limit: int = 200) -> list:
    with connect() as con:
        rows = con.execute("SELECT * FROM comments ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def list_unverified() -> list:
    """세션이 끊겨도 수동 확인이 필요한 게시를 다시 찾을 수 있게 한다."""
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM comments WHERE status='unverified' ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
