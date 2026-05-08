# soma-lecture-filter

강의 정보를 받아 LLM 기반 라우터(gateway)가 적절한 agent에게 분기해 응답을 만드는 FastAPI 서버입니다.
3개의 agent(`agent1`, `agent2`, `agent3`)는 각각 다른 담당자가 구현합니다.

---

## 디렉토리 구조

```
soma-lecture-filter/
├── backend/
│   ├── main.py              # FastAPI 진입점, /agent/run 엔드포인트
│   ├── app/
│   │   ├── schemas.py       # 요청/응답/도메인 모델 (Pydantic)
│   │   ├── llm_client.py    # AsyncOpenAI 클라이언트 (Upstage)
│   │   ├── logging_setup.py # 로깅 포맷 설정
│   │   ├── gateway.py       # 라우터 + 디스패치 (오케스트레이터)
│   │   └── agents/
│   │       ├── agent1.py    # 담당자 박성현
│   │       ├── agent2.py    # 담당자 김해울
│   │       └── agent3.py    # 담당자 이재성
│   ├── requirements.txt     # 의존성 목록
│   └── test_main.http       # API 테스트 케이스
├── frontend/
│   └── extension/           # 크롬 확장 프로그램
├── .env.example             # 환경변수 예시
└── README.md
```

---

## 시작하기

### 1. 가상환경 활성화 & 의존성 설치

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
```

### 2. `.env` 생성

루트에 `.env` 파일을 만들고 Upstage API 키를 넣습니다.
(예시는 `.env.example` 참고)

```
UPSTAGE_API_KEY=up_여러분의_키
```

> `.env`는 `.gitignore`에 등록되어 있어 커밋되지 않습니다. 절대 커밋하지 마세요.

### 3. 서버 실행

```powershell
cd backend
uvicorn main:app --reload
```

기본 주소: `http://127.0.0.1:8000`
Swagger UI: `http://127.0.0.1:8000/docs`

### 4. API 호출 테스트

`test_main.http`(JetBrains/VS Code REST Client) 파일로 바로 호출 가능,
또는 Swagger UI / curl 사용.

---

## 각 agent 담당자 가이드

> **주의:** 아래 내용은 어디까지나 **가이드라인**입니다.
> 본인 agent의 요구사항에 맞게 자유롭게 수정해도 좋습니다.
> 단, **반환 계약(`AgentResult(message=str, lectures=List[Lecture])`)** 만 지키면 gateway가 그대로 동작합니다.

### 담당 파일

- **agent1 담당자** → `backend/app/agents/agent1.py`
- **agent2 담당자** → `backend/app/agents/agent2.py`
- **agent3 담당자** → `backend/app/agents/agent3.py`

각 파일 안에 `# TODO [agentN 담당자 작성]` 박스 주석으로 수정 위치 표시되어 있습니다.

### 수정 포인트 (3곳)

#### 1) `SYSTEM_PROMPT` (모듈 상단 상수)

이 agent의 역할/지시사항을 한국어로 작성합니다.

```python
SYSTEM_PROMPT = "너는 ..."
```

#### 2) LLM 호출 메시지 구성 (`messages = [...]`)

기본 템플릿: `[system prompt, lectures 정보, ...history]` 순서로 구성됩니다.
필요 시 다른 정보 추가, 순서 변경, 일부 제거 가능합니다.

#### 3) 강의 필터링 로직 (`filtered_lectures = []`)

`req.lectures` 중 이 agent의 기준에 맞는 강의만 골라 반환합니다.
필터링이 필요 없는 경우(예: 일반 Q&A) **빈 리스트(`[]`)** 그대로 두면 됩니다.

```python
filtered_lectures = [l for l in req.lectures if 조건]
```

### 라우터 설명 갱신

`backend/app/gateway.py`의 `ROUTER_SYSTEM` 프롬프트에 각 agent 설명을 채워야
LLM이 올바르게 분기합니다 (현재는 `<placeholder description>`).

```python
ROUTER_SYSTEM = """\
You are a router. Read the user's input and pick exactly one agent:
- agent1: 강의 일정 안내
- agent2: 강의 추천
- agent3: 일반 Q&A
...
"""
```

---

## API 명세

### `POST /agent/run`

**요청**

```json
{
  "message": "다음 주 특강 뭐 있어?",
  "history": [
    { "role": "user", "content": "다음 주 특강 뭐 있어?" }
  ],
  "lectures": [
    {
      "author": "김채원, 박민우, 홍지연",
      "dateStr": "2026-05-31",
      "timeRangeStr": "20:00 ~ 22:00",
      "title": "[멘토 특강] Ch.2 — print() 한 줄이 API를 140배 느리게 만듭니다",
      "url": "https://www.swmaestro.ai/sw/mypage/mentoLec/view.do?qustnrSn=10786",
      "is_open": true
    }
  ]
}
```

**응답**

```json
{
  "message": "다음 주에 ...",
  "history": [
    { "role": "user", "content": "다음 주 특강 뭐 있어?" },
    { "role": "assistant", "content": "다음 주에 ..." }
  ],
  "lectures": [
    { "author": "...", "dateStr": "...", "...": "..." }
  ],
  "agent_used": ["agent1"]
}
```

### 필드 정의

#### Request

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `message` | string | Yes | 현재 사용자 메시지 |
| `history` | `HistoryMessage[]` | Yes | 대화 이력 (사용자 메시지 포함) |
| `lectures` | `Lecture[]` | Yes | 강의 후보 목록 |

#### Response

| 필드 | 타입 | 설명 |
|---|---|---|
| `message` | string | assistant가 생성한 응답 |
| `history` | `HistoryMessage[]` | 입력 history + 새 assistant 응답 |
| `lectures` | `Lecture[]` | agent가 필터링한 강의(없으면 `[]`) |
| `agent_used` | `string[]` | 처리에 사용된 agent 목록 (단일 agent 처리 시 `["agent1"]` 같이 1개 원소 배열, 추후 multi-agent 흐름 시 여러 원소 가능) |

#### HistoryMessage

| 필드 | 타입 | 설명 |
|---|---|---|
| `role` | string | `"user"` \| `"assistant"` |
| `content` | string | 메시지 내용 |

#### Lecture

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `author` | string | Yes | 강사 |
| `dateStr` | string | Yes | 날짜 (예: `"2026-05-31"`) |
| `timeRangeStr` | string | Yes | 시간 범위 (예: `"20:00 ~ 22:00"`) |
| `title` | string | Yes | 강의 제목 |
| `url` | string | Yes | 상세 페이지 URL |
| `is_open` | boolean \| null | No | 접수 상태 (`true`=접수중, `false`=마감, 없거나 `null`=미상) |

---

## 동작 흐름

```
client
  │
  ▼  POST /agent/run
[backend/main.py] agent_run()
  │
  ▼
[gateway.py] run_gateway()
  │
  ├─▶ [gateway.py] route()           ── LLM 호출, agent 선택
  │
  ▼
[agents/agentN.py] agentN()           ── LLM 호출 + (옵션) 강의 필터링
  │
  ▼
[gateway.py] response 조립             ── history에 assistant 메시지 추가, agent_used 기록
  │
  ▼
client (JSON 응답)
```

서버 콘솔에 각 단계별 로그가 출력됩니다:

```
12:34:56 | gateway  | received request | message='...' | history=1 | lectures=2
12:34:56 | gateway  | -> routing
12:34:56 | router   | -> LLM call (model=solar-pro3)
12:34:57 | router   | selected: agent1
12:34:57 | gateway  | -> dispatching to agent1
12:34:57 | agent1   | start | history=1 | lectures=2
12:34:57 | agent1   | -> LLM call (model=solar-pro3, messages=4)
12:34:58 | agent1   | LLM response received (143 chars)
12:34:58 | agent1   | filtered lectures: 0
12:34:58 | gateway  | response ready | agent=agent1 | message_len=143 | lectures=0
```

---

## 사용 모델

- LLM: `solar-pro3` (Upstage)
- API base URL: `https://api.upstage.ai/v1`
- SDK: `openai==1.52.2` (OpenAI 호환 인터페이스로 Upstage 호출)
