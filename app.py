"""유튜브 댓글 에이전트 대시보드 — 실행: streamlit run app.py"""
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st
import yaml
from dotenv import load_dotenv

import db
import drafter
import youtube_api as yt

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
YT_KEY = os.getenv("YOUTUBE_API_KEY")
PROFILE_NAMES = [p["name"] for p in CFG["profiles"]]
db.init()

st.set_page_config(page_title="댓글 에이전트", layout="wide")
st.markdown("""
<style>
  :root { --ink: #19243d; --blue: #3159c9; --line: #dce3ef; }
  .stApp { background: #f7f9fd; color: var(--ink); font-family: 'Malgun Gothic', 'Noto Sans KR', sans-serif; }
  .block-container { padding-top: 2.2rem; max-width: 1700px; }
  h1, h2, h3 { color: var(--ink); letter-spacing: -0.035em; }
  h1 { font-weight: 800; }
  [data-testid="stVerticalBlockBorderWrapper"] { border-color: var(--line); }
  [data-testid="stMetric"] { background: #eaf0ff; padding: 1rem; border-radius: .65rem; }
  .stButton > button[kind="primary"] { background: var(--blue); border-color: var(--blue); }
  .stButton > button[kind="primary"]:disabled { background: #dce3ef; border-color: #dce3ef; color: #65728b; }
  .stButton > button:focus-visible { outline: 3px solid #87a8ff; outline-offset: 2px; }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=300, show_spinner=False)
def cached_latest(key: str, keyword: str, count: int):
    return yt.search_latest(key, keyword, count)


@st.cache_data(ttl=300, show_spinner=False)
def cached_rising(key: str, keyword: str, low: int, high: int, per_hour: int, hours: int):
    return yt.find_rising(key, keyword, low, high, per_hour, hours)


ss = st.session_state
for k, v in {"videos": [], "selected": None, "drafts": {}, "job_id": None, "ver": 0, "errs": ["초안 없음"], "text": ""}.items():
    ss.setdefault(k, v)


def fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso


def watch_url(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


@st.fragment(run_every=1.5)
def status_panel():
    jid = ss.get("job_id")
    if not jid:
        return
    job = db.get_job(jid)
    if not job:
        return
    order = ["queued", "opening", "typing", "submitting", "posted"]
    labels = {"queued": "대기", "opening": "프로필 여는 중", "typing": "입력 중", "submitting": "게시 확인 중", "posted": "게시 완료"}
    s = job["status"]
    st.markdown("**진행 상태**")
    if s in order:
        idx = order.index(s)
        st.progress((idx + 1) / len(order))
        st.write("  →  ".join(f"**{labels[x]}**" if i == idx else labels[x] for i, x in enumerate(order[1:], 1)))
        if job.get("detail") and s != "posted":
            st.caption(job["detail"])
    if s == "posted":
        st.success("게시 완료 · 내 댓글이 확인됐습니다.")
        st.link_button("댓글 보기", job.get("comment_url") or watch_url(job["video_id"]), use_container_width=True)
    elif s in ("failed", "unverified"):
        st.error(f"{db.STATUS_KO[s]}: {job.get('detail') or ''}")
        if job.get("screenshot") and Path(job["screenshot"]).exists():
            st.image(job["screenshot"], caption="실패 시점 화면")
        if s == "unverified":
            st.link_button("영상에서 직접 확인", watch_url(job["video_id"]))
            yes, no = st.columns(2)
            if yes.button("댓글 확인됨", key=f"confirm_{jid}"):
                db.resolve_unverified(jid, True)
                st.rerun()
            if no.button("댓글 없음 확인", key=f"not_posted_{jid}"):
                db.resolve_unverified(jid, False)
                st.rerun()
    # 작업이 끝나면 한 번 전체 새로고침 (목록·카운트 갱신)
    if s in ("posted", "failed", "unverified") and not ss.get(f"done_{jid}"):
        ss[f"done_{jid}"] = True
        st.rerun()


st.title("유튜브 댓글 에이전트")
st.caption("① 영상을 불러오고 → ② 목록에서 하나를 고르면 초안이 만들어지고 → ③ [댓글 달기]를 누르면 크롬이 열려 직접 게시합니다.")
if not YT_KEY or not os.getenv("ANTHROPIC_API_KEY"):
    missing = ", ".join(k for k, ready in (("YOUTUBE_API_KEY", YT_KEY), ("ANTHROPIC_API_KEY", os.getenv("ANTHROPIC_API_KEY"))) if not ready)
    st.info(f"시작하려면 .env에 {missing}를 입력하세요. 게시 전에 해당 크롬 프로필에 로그인도 필요합니다.")
st.caption("광고임을 밝히는 댓글만 게시합니다. 시연은 본인 채널의 테스트 영상에서 진행하세요.")

c1, c2, c3 = st.columns([1.15, 1.25, 1])

# ① 영상 불러오기 ----------------------------------------------------------
with c1:
    st.subheader("① 영상 불러오기")
    b1, b2 = st.columns(2)
    if b1.button("영상 불러오기", type="primary", use_container_width=True):
        with st.spinner("최신 영상을 불러오는 중..."):
            try:
                ss.videos = cached_latest(YT_KEY, CFG["search"]["keyword"], CFG["search"]["max_results"])
            except Exception as e:
                st.error(f"불러오기 실패: {e}")
    if b2.button("🔥 뜨기 직전 영상", use_container_width=True, help="조회수 1,000~5,000 · 시간당 150회 이상 (초안까지만)"):
        r = CFG["rising"]
        with st.spinner("뜨기 직전 영상을 찾는 중..."):
            try:
                ss.videos = cached_rising(YT_KEY, CFG["search"]["keyword"], r["min_views"], r["max_views"],
                                           r["min_views_per_hour"], r["lookback_hours"])
                if not ss.videos:
                    st.info("조건에 맞는 영상이 지금은 없습니다.")
            except Exception as e:
                st.error(f"검색 실패: {e}")

    done = db.commented_video_ids()
    with st.container(height=680 if ss.videos else 190):
        if not ss.videos:
            st.caption("버튼을 누르면 최신 영상이 여기에 뜹니다.")
        for v in ss.videos:
            with st.container(border=True):
                ic, tc = st.columns([1, 1.7])
                if v["thumbnail"]:
                    ic.image(v["thumbnail"], use_container_width=True)
                tc.markdown(f"**{v['title']}**")
                tc.caption(f"{v['channel']} · {fmt_date(v['published_at'])} · 조회 {v['view_count']:,}")
                if v.get("rising"):
                    tc.caption(f"🔥 시간당 약 {v['views_per_hour']:.0f}회")
                if v["id"] in done:
                    tc.button("✅ 댓글 완료", key=f"done_{v['id']}", disabled=True)
                elif tc.button("선택", key=f"sel_{v['id']}"):
                    ss.selected = v

# ② 영상 선택 ---------------------------------------------------------------
with c2:
    st.subheader("② 영상 선택")
    with st.expander("🔗 링크로 직접 입력"):
        url = st.text_input("유튜브 링크", placeholder="https://www.youtube.com/watch?v=...")
        if st.button("이 영상 불러오기"):
            vid = yt.parse_video_id(url)
            if not vid:
                st.error("링크에서 영상 ID를 찾지 못했습니다.")
            else:
                try:
                    found = yt.get_videos(YT_KEY, [vid])
                    if found:
                        ss.selected = found[0]
                    else:
                        st.error("영상을 찾지 못했습니다.")
                except Exception as e:
                    st.error(str(e))

    v = ss.selected
    if not v:
        st.info("왼쪽 목록에서 영상을 고르세요.")
        ss.errs, ss.text = ["영상 미선택"], ""
    else:
        if v["thumbnail"]:
            st.image(v["thumbnail"], use_container_width=True)
        st.markdown(f"**{v['title']}**")
        st.caption(f"{v['channel']} · {fmt_date(v['published_at'])}")
        st.link_button("유튜브에서 보기", watch_url(v["id"]))

        regen = st.button("🔄 초안 다시 만들기")
        if regen or v["id"] not in ss.drafts:
            with st.spinner("자막과 인기 댓글을 읽고 초안을 쓰는 중..."):
                try:
                    d = drafter.make_draft(v, CFG, previous=db.previous_texts())
                    ss.ver += 1
                    d["ver"] = ss.ver
                    ss.drafts[v["id"]] = d
                except Exception as e:
                    st.error(f"초안 생성 실패: {e}")

        d = ss.drafts.get(v["id"])
        if d:
            st.markdown("**영상 속 고민 한 줄**")
            st.success(d["concern"] or "(정리 실패)")
            st.caption(f"짚은 장면/말: {d['detail']}  ·  참고 자료: {d['source']}")
            text = st.text_area("댓글 초안 (직접 고쳐도 됩니다)", value=d["comment"],
                                key=f"txt_{v['id']}_{d['ver']}", height=130)
            errs = drafter.validate(text, CFG, [t for t in db.previous_texts()])
            st.caption(f"{len(text)}자 / {CFG['comment']['min_chars']}~{CFG['comment']['max_chars']}자")
            for e in errs:
                st.warning(e)
            if not errs:
                st.caption("✔ 규칙 통과")
            ss.errs, ss.text = errs, text
        else:
            ss.errs, ss.text = ["초안 없음"], ""

# ③ 댓글 달기 ---------------------------------------------------------------
with c3:
    st.subheader("③ 댓글 달기")
    limit = CFG["limits"]["daily_max_comments"]
    st.metric("오늘 게시", f"{db.count_today()} / {limit}")
    choice = st.selectbox("계정 (크롬 프로필)", ["자동 배정 (영상마다 순환)"] + PROFILE_NAMES)
    profile = db.next_profile(PROFILE_NAMES) if choice.startswith("자동") else choice
    st.caption(f"이번에 쓸 프로필: **{profile}**")

    running = False
    if ss.job_id:
        j = db.get_job(ss.job_id)
        running = bool(j and j["status"] in ("queued", "opening", "typing", "submitting"))

    v = ss.selected
    reason = None
    if not v:
        reason = "먼저 ②에서 영상을 고르세요."
    elif v.get("rising"):
        reason = "뜨기 직전 영상은 초안까지만 만듭니다."
    elif ss.errs:
        reason = "초안이 규칙을 통과해야 게시할 수 있습니다."
    elif running:
        reason = "진행 중인 작업이 끝난 뒤 다시 눌러 주세요."

    if st.button("댓글 달기", type="primary", use_container_width=True, disabled=bool(reason)):
        try:
            jid = db.reserve(v["id"], v["title"], profile, ss.text, limit)
            pdir = next(p["user_data_dir"] for p in CFG["profiles"] if p["name"] == profile)
            t = CFG["typing"]
            subprocess.Popen(
                [sys.executable, str(ROOT / "poster.py"), "--job-id", str(jid),
                 "--user-data-dir", str((ROOT / pdir).resolve()),
                 "--min-delay", str(t["min_delay_ms"]), "--max-delay", str(t["max_delay_ms"]),
                 "--hold", str(t["hold_seconds"])],
                cwd=str(ROOT),
            )
            ss.job_id = jid
        except db.RuleViolation as e:
            st.error(str(e))
        except Exception as e:
            if 'jid' in locals():
                db.update_status(jid, "failed", f"게시 프로세스를 시작하지 못했습니다: {e}")
            st.error(f"게시 시작 실패: {e}")
    if reason:
        st.caption(reason)
    status_panel()

# 기록 ----------------------------------------------------------------------
st.divider()
st.subheader("기록")
rows = db.list_log()
if rows:
    st.dataframe(
        [
            {
                "시간": r["created_at"].replace("T", " "),
                "영상": r["video_title"],
                "프로필": r["profile"],
                "댓글": r["text"],
                "결과": db.STATUS_KO.get(r["status"], r["status"]),
                "비고": r["detail"] or "",
            }
            for r in rows
        ],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.caption("아직 기록이 없습니다.")
