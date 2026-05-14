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
│   │       ├── _common.py   # JSON index 응답 파서 (agent2/3 공용)
│   │       ├── agent1.py    # 담당자 박성현 — 접수중 강의 결정론적 필터
│   │       ├── agent2.py    # 담당자 김해울 — 일정(날짜·시간) 기반 필터
│   │       └── agent3.py    # 담당자 이재성 — 관심사 기반 추천
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

> **반환 계약:** `AgentResult(message=str, lectures=List[Lecture])` 만 지키면
> gateway가 단일/복합 의도 모두 처리합니다. gateway가 진입 시 `req.lectures`를
> 날짜순으로 정렬해 모든 agent에게 전달하므로, 결과를 다시 정렬할 필요 없습니다.

### 담당 파일

- **agent1** (`agents/agent1.py`) — 결정론적 필터. LLM 호출 없음. `is_open=True`만 선택.
- **agent2** (`agents/agent2.py`) — LLM 기반 일정 필터. JSON `{"message", "indices"}` 출력 + `parse_index_response`로 1-based index 매칭.
- **agent3** (`agents/agent3.py`) — LLM 기반 관심사 추천. agent2와 동일한 JSON index 패턴.

### LLM 호출 패턴 (agent2/3)

```python
resp = await client.chat.completions.create(
    model="solar-pro3",
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},  # 또는 _build_system_prompt(now)
        {"role": "system", "content": f"Available lectures:\n{lectures_text}"},
        *[{"role": h.role, "content": h.content} for h in req.history[-4:]],  # 최근 2턴
        {"role": "user", "content": req.message},
    ],
    response_format={"type": "json_object"},
)
raw = resp.choices[0].message.content or ""
message, filtered_lectures = parse_index_response(raw, req.lectures)
return AgentResult(message=message, lectures=filtered_lectures)
```

핵심 포인트:
- 각 강의를 `[#N] [상태] 제목 (날짜 시간, 강사)` 형식으로 1-based index와 함께 LLM에 제시.
- LLM은 `{"message": "...", "indices": [1, 3, 5]}` 형태로만 응답하도록 SYSTEM_PROMPT에서 강제.
- `parse_index_response`가 range/중복/비정수 인덱스를 자동으로 걸러내 환각을 방지.

### 라우터 가이드

`backend/app/gateway.py`의 `ROUTER_SYSTEM_TC`는 tool-calling 라우터 프롬프트로, 우선순위 기반으로 다음 도구를 선택합니다:

| 단서 | 도구 | 매핑 agent |
|---|---|---|
| 주제·기술·직무 (기획/백엔드/ML 등) | `recommend_lectures_by_interest` | agent3 |
| 날짜·요일·시간대 (5월 15일/내일/저녁 등) | `filter_lectures_by_schedule` | agent2 |
| 단순 "접수중 강의" | `list_open_lectures` | agent1 |
| 의도가 모호함 | `ask_clarification` | (없음, 사용자에게 되묻는 메시지 반환) |

복합 의도(예: "다음 주 저녁 ML 강의")는 여러 도구를 동시 호출 → 각 agent 결과의 URL 교집합(`_intersect_lectures`)이 최종 반환됩니다.

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
  "agent_used": ["filter_lectures_by_schedule"]
}
```

> `agent_used` 값은 라우터가 선택한 tool 이름입니다: `list_open_lectures` (→agent1), `filter_lectures_by_schedule` (→agent2), `recommend_lectures_by_interest` (→agent3). clarification 경로에서는 `[]`.

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
| `agent_used` | `string[]` | 처리에 사용된 tool 이름 목록. **빈 배열 `[]`** = clarification 경로(라우터가 의도가 모호하다고 판단해 사용자에게 되묻는 메시지 반환). **1개** = 단일 의도. **여러 개** = 복합 의도(여러 agent 결과의 URL 교집합 반환). 프론트엔드는 이 길이로 메시지/카드 표시 분기 |

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
  ├─▶ req.lectures를 (dateStr, timeRangeStr) 키로 정렬
  │
  ├─▶ _route_with_tools()                ── tool-calling LLM (solar-pro3)
  │     │
  │     ├─ ask_clarification 선택 → (return) agent_used=[], lectures=[],
  │     │                                    message=명확화 질문
  │     │
  │     └─ 1개 이상 일반 도구 선택 → tool_names
  │
  ├─▶ asyncio.gather(agent1/2/3 병렬 실행, timeout=60s)
  │
  ├─▶ 결과 통합
  │     ├─ 0개 성공: "강의 정보 처리에 실패했습니다."
  │     ├─ 1개 성공: 그대로 사용
  │     └─ 2개+ 성공: _intersect_lectures (URL 교집합) + _synthesize 합성 메시지
  │
  ├─▶ final_lectures 다시 한 번 날짜순 정렬
  │
  ├─▶ _format_history_content: 합성 메시지 + "[직전 안내한 강의]" 요약을
  │   assistant history에 임베드 (후속 질의 컨텍스트 보존)
  │
  ▼
client (JSON 응답: message / history / lectures / agent_used)
```

서버 콘솔 로그 예:

```
12:34:56 | gateway  | received request | message='5월 15일 강의' | history=0 | lectures=42
12:34:56 | gateway  | -> routing
12:34:56 | router   | -> tool-calling LLM call (model=solar-pro3)
12:34:57 | router   | selected tools: ['filter_lectures_by_schedule']
12:34:57 | gateway  | -> dispatching to ['filter_lectures_by_schedule']
12:34:57 | agent2   | start | history=0 | lectures=42
12:34:57 | agent2   | -> LLM call (model=solar-pro3, messages=3)
12:34:58 | agent2   | LLM response received (84 chars): {"message":"...", "indices":[7,12]}
12:34:58 | agents   | parsed indices=2 | matched=2 (of 42 candidates)
12:34:58 | gateway  | response ready | tools=['filter_lectures_by_schedule'] | message_len=24 | history_content_len=180 | lectures=2
```

---

## 사용 모델

- LLM: `solar-pro3` (Upstage)
- API base URL: `https://api.upstage.ai/v1`
- SDK: `openai==1.52.2` (OpenAI 호환 인터페이스로 Upstage 호출)
