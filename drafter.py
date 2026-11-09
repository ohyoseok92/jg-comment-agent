"""② 영상 선택 — 자막/제목·설명 + 인기 댓글을 읽고 고민 한 줄과 댓글 초안을 만든다."""
import json
import os
import re
from difflib import SequenceMatcher

import requests

import youtube_api as yt

URL_RE = re.compile(r"https?://|www\.|\b[\w-]+\.(com|kr|net|co|me|io|ly|link)\b", re.I)
# 운명원을 직접 이용해 본 것처럼 지어낸 후기 표현
FAKE_REVIEW_RE = re.compile(
    r"(운명원|사주|상담).{0,20}(봤는데|봤더니|받아봤|받았는데|다녀왔|보고\s?왔|가봤|해봤|맞더라|맞았|맞춰|소름)"
)
SIMILARITY_LIMIT = 0.6

SYSTEM = """너는 운명원 홍보 담당자의 공개 광고 댓글을 돕는 편집자다.
시청자인 척하거나 영상 전체를 보았다고 암시하지 않는다. 확인된 자막·제목·설명만 근거로 삼는다.
과장과 가짜 이용 후기를 쓰지 않는다. 댓글은 읽기 쉬운 자연스러운 한국어로 쓴다.
출력은 반드시 JSON 하나만."""

OLLAMA_URL = "http://127.0.0.1:11434"


def local_model_status(model: str) -> tuple[bool, str]:
    """실제로 PC에 저장된 모델만 사용한다. 클라우드 모델 자동 호출을 피한다."""
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        response.raise_for_status()
        names = {item["name"] for item in response.json().get("models", [])}
    except requests.RequestException:
        return False, "Ollama가 실행 중이지 않습니다. PowerShell에서 ollama serve를 실행하세요."
    if model not in names:
        return False, f"로컬 모델 {model}을 찾지 못했습니다. ollama list로 설치된 이름을 확인하세요."
    return True, "로컬 AI 준비됨"


def _chat(provider: str, model: str, messages: list, max_tokens: int) -> str:
    if provider == "ollama":
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": SYSTEM}, *messages],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.4, "num_ctx": 4096, "num_predict": max_tokens},
        }
        try:
            response = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=180)
            response.raise_for_status()
            return response.json()["message"]["content"]
        except (requests.RequestException, KeyError, ValueError) as exc:
            raise RuntimeError(f"로컬 AI 호출 실패: {exc}") from exc
    if provider == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY가 .env에 없습니다.")
        import anthropic

        client = anthropic.Anthropic(api_key=key)
        response = client.messages.create(
            model=model, max_tokens=max_tokens, system=SYSTEM, messages=messages
        )
        return "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
    raise RuntimeError(f"지원하지 않는 LLM_PROVIDER: {provider}")


def _limits(cfg):
    c = cfg["comment"]
    prefix = (c.get("disclosure") or "").strip()
    extra = len(prefix) + 1 if prefix else 0
    return c["min_chars"] - extra, c["max_chars"] - extra, prefix


def compose(prefix: str, body: str) -> str:
    body = body.strip()
    return f"{prefix} {body}" if prefix else body


def validate(text: str, cfg: dict, previous=()) -> list:
    """게시 전 규칙 검사. 빈 리스트면 통과."""
    c = cfg["comment"]
    errs = []
    n = len(text)
    if not (c["min_chars"] <= n <= c["max_chars"]):
        errs.append(f"길이 {n}자 — {c['min_chars']}~{c['max_chars']}자여야 합니다.")
    if c["brand"] not in text:
        errs.append(f"'{c['brand']}'이 들어가야 합니다.")
    if not re.search(r"연애\s*사주", text):
        errs.append("운명원이 어떤 서비스인지 알 수 있도록 '연애 사주'를 넣어야 합니다.")
    prefix = (c.get("disclosure") or "").strip()
    if prefix and not text.startswith(prefix):
        errs.append(f"광고 표시 '{prefix}'로 시작해야 합니다.")
    if URL_RE.search(text):
        errs.append("링크는 넣을 수 없습니다.")
    if "#" in text:
        errs.append("해시태그는 넣을 수 없습니다.")
    if FAKE_REVIEW_RE.search(text):
        errs.append("운명원을 직접 이용해 본 것 같은 후기 표현은 쓸 수 없습니다.")
    if re.search(r"운명원\s*님", text):
        errs.append("운명원은 사람 이름이 아니라 서비스 이름입니다.")
    if re.search(r"운명원.{0,20}(알려주|설명하|분석하|영상|이론|해결|보장)", text):
        errs.append("운명원이 영상 제공자이거나 고민을 해결해 준다는 근거 없는 표현을 쓰지 마세요.")
    for p in previous:
        if p and p != text and SequenceMatcher(None, text, p).ratio() > SIMILARITY_LIMIT:
            errs.append("이전에 단 댓글과 너무 비슷합니다.")
            break
    return errs


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("AI 응답에서 JSON을 찾지 못했습니다.")
    return json.loads(m.group(0))


def make_draft(video: dict, cfg: dict, previous=(), max_tries: int = 4) -> dict:
    provider = os.getenv("LLM_PROVIDER", "ollama").lower()
    yt_key = os.getenv("YOUTUBE_API_KEY")
    model = (os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b-instruct-q4_K_M") if provider == "ollama"
             else os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"))
    if provider == "ollama":
        ready, detail = local_model_status(model)
        if not ready:
            raise RuntimeError(detail)

    transcript = yt.get_transcript(video["id"], max_chars=2500 if provider == "ollama" else 6000)
    comments = yt.get_top_comments(yt_key, video["id"], n=6 if provider == "ollama" else 15) if yt_key else []
    source = "자막" if transcript else "제목·설명"
    lo, hi, prefix = _limits(cfg)
    brand = cfg["comment"]["brand"]

    top = "\n".join(f"- ({c['likes']}♥) {c['text'][:150]}" for c in comments) or "(없음)"
    prev = "\n".join(f"- {p}" for p in list(previous)[:10]) or "(없음)"
    context = f"""[영상 정보]
제목: {video['title']}
채널: {video['channel']}
설명: {video.get('description', '')[:800]}

[자막]
{transcript or '(자막 없음 — 제목과 설명만 참고)'}

[인기 댓글]
{top}"""

    analysis_prompt = context + """

[할 일]
영상이 다루는 연애 고민을 concern 한 줄(35자 이내)로 정리한다.
자막·제목·설명에 실제로 적힌 20자 이내의 표현을 detail로 원문 그대로 고른다.
인기 댓글은 관심사를 파악하는 참고 자료일 뿐 영상 내용의 증거가 아니다.
확인할 수 없는 장면은 지어내지 말고 detail에 제목의 표현을 그대로 쓴다.
출력: {"concern": "...", "detail": "..."}"""
    evidence = f"{video['title']} {video.get('description', '')[:800]} {transcript or ''}"
    concern, detail = "", ""
    for attempt in range(2):
        raw_analysis = _chat(
            provider, model,
            [{"role": "user", "content": analysis_prompt + ("\n직전 답변은 자료에 없는 표현이었습니다. detail은 원문에서 정확히 복사하세요." if attempt else "")}],
            400,
        )
        try:
            analysis = _parse_json(raw_analysis)
        except (ValueError, AttributeError):
            continue
        concern = str(analysis.get("concern", "")).strip()[:35]
        detail = str(analysis.get("detail", "")).strip()
        if detail in evidence and len(detail) > 20:
            detail = detail[:20].rsplit(" ", 1)[0].rstrip(".,!? ") or detail[:20]
        if concern and detail and detail in evidence and "#" not in detail and not URL_RE.search(detail):
            break
    else:
        detail = video["title"].split("#", 1)[0].strip()[:20].rstrip(".,!? ")
        concern = concern or video["title"].strip()[:35]
        source = "제목"
        if not detail:
            raise ValueError("영상에서 댓글에 넣을 표현을 찾지 못했습니다.")

    prompt = f"""{context}

[확인된 고민과 장면]
고민: {concern}
장면 또는 표현: {detail}

[이전에 단 댓글 — 이것들과 표현이 겹치지 않게]
{prev}

[할 일]
body: 운명원 담당자가 공개 광고 표시와 함께 쓸 댓글 본문.
   - 길이 {lo}~{hi}자 (공백 포함)
   - 확인된 장면 또는 표현 '{detail}'을 그대로 넣을 것
   - '{brand}'은 이 영상과 무관한 별도 연애 사주 서비스다. '운명원 연애 사주'라고 소개할 것
   - 운명원이 영상이나 이론을 알려주거나 고민을 해결·보장한다는 식으로 쓰지 말 것. 사람처럼 '님'을 붙이지 말 것
   - 연락을 요청하지 말고, 연애 사주를 알아볼 선택지로만 소개할 것
   - 제삼자 추천이나 이용 경험을 가장하지 말 것
   - 금지: 링크, 해시태그, 가짜 후기, 과장 광고 문구, 인기 댓글 베끼기
   - 광고 표시 문구는 시스템이 앞에 붙이므로 body에는 넣지 말 것

출력 형식: {{"body": "..."}}"""

    messages = [{"role": "user", "content": prompt}]
    last = None
    for _ in range(max_tries):
        raw = _chat(provider, model, messages, 500)
        try:
            data = _parse_json(raw)
            body = str(data.get("body", "")).strip()
            comment = compose(prefix, body)
            errs = validate(comment, cfg, previous)
            if detail not in body:
                errs.append("확인된 장면/말을 댓글 본문에 그대로 넣어야 합니다.")
        except (ValueError, json.JSONDecodeError) as e:
            data, comment, errs = {}, "", [str(e)]
        last = {
            "concern": concern,
            "detail": detail,
            "comment": comment,
            "source": source,
            "errors": errs,
        }
        if not errs:
            return last
        messages += [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "다음 문제를 고쳐서 같은 JSON 형식으로 다시 써줘:\n- " + "\n- ".join(errs)},
        ]
    return last  # 끝까지 통과 못 하면 오류와 함께 반환 → 화면에서 수정 가능
