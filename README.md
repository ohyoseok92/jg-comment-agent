# 유튜브 댓글 에이전트 대시보드

지원자 PC에서 실행하는 Streamlit 대시보드입니다. 최신 영상 검색, 영상 내용에 맞춘 댓글 초안, 별도 Chrome 프로필을 이용한 게시와 확인을 세 칸에 배치했습니다. 게시 댓글에는 `[광고]` 표시가 붙습니다.

## Windows에서 실행

PowerShell에서 이 폴더를 연 뒤 실행합니다.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

비용을 아끼려면 `.env`에는 `YOUTUBE_API_KEY` 한 개만 입력합니다. 기본 AI는 PC에 설치된 Ollama 모델이므로 AI API 키가 필요하지 않습니다. 이 PC에는 `qwen2.5-coder:7b-instruct-q4_K_M` 모델이 이미 있습니다. 다른 PC에서는 `ollama pull qwen2.5-coder:7b-instruct-q4_K_M`로 설치할 수 있습니다. Ollama가 실행 중이지 않으면 별도 PowerShell 창에서 `ollama serve`를 실행합니다. [Ollama 로컬 API](https://docs.ollama.com/api/authentication)는 인증 키가 필요하지 않습니다.

YouTube Data API v3는 Google Cloud 프로젝트에서 활성화하고 API 키를 만듭니다. Google 계정으로 [Google Cloud 콘솔](https://console.cloud.google.com/apis/library/youtube.googleapis.com)에 접속해 서비스를 활성화한 뒤 [사용자 인증 정보](https://console.cloud.google.com/apis/credentials)에서 API 키를 생성합니다. 키의 API 제한을 `YouTube Data API v3`로 설정하고 `.env`의 `YOUTUBE_API_KEY=` 뒤에 붙여 넣습니다. 검색 결과는 5분간 캐시합니다. [공식 인증 안내](https://developers.google.com/youtube/data/guides/authentication)를 참고하세요.

유료 Claude API를 쓰고 싶을 때만 `LLM_PROVIDER=anthropic`으로 바꾸고 `ANTHROPIC_API_KEY`를 입력합니다. 기본 설정에서는 이 키가 필요하지 않습니다.

처음 한 번은 게시에 쓸 별도 Chrome 프로필을 엽니다.

```powershell
.\.venv\Scripts\python.exe open_profile.py 유튜브1
```

Chrome에서 직접 Google 계정에 로그인하고 YouTube 화면을 확인한 다음 창을 완전히 닫습니다. 다른 계정을 쓰려면 `유튜브2`, `유튜브3`도 같은 방식으로 준비합니다. 같은 프로필의 Chrome 창이 열려 있으면 Playwright가 게시 작업을 시작할 수 없습니다.

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

대시보드 주소는 기본적으로 `http://localhost:8501`입니다. 시연은 본인 채널의 테스트 영상에서 진행하고, 실제 게시 전에 초안과 광고 표시를 직접 확인하세요.

## 화면 사용

1. `영상 불러오기`로 최신 영상을 찾거나, 가운데 칸에 영상 링크를 넣습니다.
2. 영상을 고르면 AI가 자막 또는 제목·설명에서 고민과 확인 가능한 표현을 고르고, 이를 이용해 댓글 초안을 만듭니다. 인기 댓글은 관심사 파악에만 사용합니다. 초안은 직접 수정할 수 있습니다.
3. `댓글 달기`를 누르면 준비한 Chrome 프로필이 열립니다. 입력, 게시, 댓글 확인 상태가 대시보드에 표시됩니다.

게시 확인이 실패하면 중복 게시를 막기 위해 해당 영상은 잠깁니다. `영상에서 직접 확인`을 누르고 댓글 목록을 살핀 뒤 `댓글 확인됨` 또는 `댓글 없음 확인`을 선택하세요. 실패 화면은 `data/screenshots/`에 저장됩니다.

## 운영 규칙

- 영상별 활성 게시 작업은 SQLite 부분 UNIQUE 인덱스로 하나만 허용합니다.
- 하루 상한은 `config.yaml`의 `daily_max_comments`이며, 예약과 건수 확인을 한 트랜잭션에서 처리합니다.
- 게시 버튼을 누르기 직전에 상태를 `submitting`으로 기록합니다. 그 뒤 프로세스가 멈추면 수동 확인 전까지 재시도를 막습니다.
- 댓글은 40~120자, `운명원` 포함, 광고 표시 필수입니다. 링크·해시태그·가짜 이용 후기는 검증에서 차단합니다.
- 프로필 자동 순환, 링크 직접 입력, 뜨기 직전 영상 필터를 제공합니다. 뜨기 직전 영상은 초안까지만 만듭니다.

## 검증과 제출

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

오프라인 테스트는 DB 중복·상한·불확정 상태, 영상 필터, URL 파싱, AI 초안 규칙을 확인합니다. 로컬 Ollama 초안 생성은 API 키 없이 시험할 수 있습니다. 실제 YouTube 검색과 게시 성공은 YouTube API 키와 로그인된 Chrome 프로필이 있어야 확인할 수 있습니다. 제출에는 2~3분 시연 영상이 필수입니다. [시연 촬영 가이드](시연_촬영_가이드.md)와 [제작 의도](제작의도.md)를 참고하세요.

`.env`, `chrome_profiles/`, `data/`, `.venv/`는 Git에서 제외합니다.

설정만 먼저 확인하려면 `.\.venv\Scripts\python.exe check_setup.py`를 실행하세요. 이 명령은 YouTube API를 호출하거나 댓글을 게시하지 않습니다.

게시 없이 Chrome 입력 단계만 확인하려면 `.\.venv\Scripts\python.exe preview_typing.py <유튜브 영상 URL>`을 실행하세요. 미리 정해진 테스트 문구를 입력하고 화면을 캡처한 뒤 창을 닫습니다. 게시 버튼은 누르지 않습니다.

## 사용한 오픈소스와 API

Claude가 만든 초기 코드 초안을 검토·수정했습니다. 사용한 주요 도구는 [Streamlit](https://streamlit.io/), [Playwright](https://playwright.dev/python/), [Ollama](https://ollama.com/), [youtube-transcript-api](https://github.com/jdepoix/youtube-transcript-api), [Requests](https://requests.readthedocs.io/), [PyYAML](https://pyyaml.org/), [python-dotenv](https://github.com/theskumar/python-dotenv), 선택 사항인 [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python), [YouTube Data API v3](https://developers.google.com/youtube/v3)입니다.
