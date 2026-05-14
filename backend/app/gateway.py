import asyncio
import json
import logging
import os
from typing import Awaitable, Callable, List, Optional, Tuple

from openai import BadRequestError

from app import agents
from app.llm_client import llm_call
from app.schemas import AgentRequest, AgentResponse, AgentResult, HistoryMessage, Lecture

log = logging.getLogger("gateway")
router_log = logging.getLogger("router")
synth_log = logging.getLogger("synth")


AGENT_TIMEOUT_S = float(os.getenv("AGENT_TIMEOUT_S", "35"))


LECTURE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_open_lectures",
            "description": (
                "질문에 '접수중', '열려있는', '신청 가능한', '오픈된' 등의 접수 상태 조건이 포함되어 있으면 호출한다. "
                "단독으로 쓰이거나, 일정/주제 조건과 결합된 복합 질의(예: '이번 달 열려있는 네트워크 강의')에서도 함께 다중 호출해야 한다."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "filter_lectures_by_schedule",
            "description": (
                "질문에 특정 날짜·기간·요일·시간대('이번 달', '내일', '5월 20일', 'X월 Y일 사이', '저녁') 등의 일정/시간/기간 조건이 포함되어 있으면 호출한다. "
                "접수 상태나 주제 조건과 결합된 복합 질의(예: '이번 달 열려있는 네트워크 강의')에서도 함께 다중 호출해야 한다."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recommend_lectures_by_interest",
            "description": (
                "질문에 특정 기술·주제·도메인·관심사('네트워크', 'HTTP', 'DevOps', 'ML', '백엔드', 'DB', '디비', '데이터베이스', '프론트엔드', 'AI', '기획', 'UI/UX', '디자인', '프로덕트' 등) 조건이 포함되어 있으면 호출한다. "
                "접수 상태나 일정 조건과 결합된 복합 질의(예: '이번 달 열려있는 네트워크 강의')에서도 함께 다중 호출해야 한다."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


AgentHandler = Callable[[AgentRequest], Awaitable[AgentResult]]

TOOL_NAME_TO_AGENT: dict[str, AgentHandler] = {
    "list_open_lectures": agents.agent1,
    "filter_lectures_by_schedule": agents.agent2,
    "recommend_lectures_by_interest": agents.agent3,
}

LEGACY_TO_TOOL = {
    "agent1": "list_open_lectures",
    "agent2": "filter_lectures_by_schedule",
    "agent3": "recommend_lectures_by_interest",
}

DEFAULT_TOOL = "list_open_lectures"


ROUTER_SYSTEM_TC = """\
너는 SOMA 강의 라우터다. 사용자의 요청을 분석하여 해당하는 모든 도구를 동시에 호출하라.
사용자 질의는 다음 3가지 조건의 조합으로 이루어질 수 있으며, 포함된 조건에 해당하는 도구를 모두 병렬로(Parallel tool calls) 호출해야 한다.

1. 접수 상태 조건 ('열려있는', '접수중', '신청 가능한' 등) -> `list_open_lectures` 호출
2. 일정/시간/기간 조건 ('이번 달', '다음 주', '내일', '5월 20일', 'X월 Y일 사이' 등) -> `filter_lectures_by_schedule` 호출
3. 주제/도메인/관심사 조건 ('네트워크', 'HTTP', 'DevOps', 'ML', '백엔드', 'DB', '디비', '데이터베이스', '프론트엔드', '기획', '디자인' 등) -> `recommend_lectures_by_interest` 호출

예시 1) "이번달 열려있는 네트워크 관련 강의 알려줘"
-> 접수 상태 + 일정 + 주제 조건이 모두 있으므로 `list_open_lectures`, `filter_lectures_by_schedule`, `recommend_lectures_by_interest` 3개의 도구를 동시에 모두 호출해야 한다.

예시 2) "다음 주 ML 강의 추천해줘"
-> 일정 + 주제 조건이 있으므로 `filter_lectures_by_schedule`, `recommend_lectures_by_interest` 2개의 도구를 동시에 호출해야 한다.

예시 3) "5월 20일이랑 5월 23일 사이 기획 강의 있으면 알려줘"
-> 일정 조건 + 주제 조건 + 접수 상태 조건이 결합된 복합 질의이므로 `list_open_lectures`, `filter_lectures_by_schedule`, `recommend_lectures_by_interest` 3개의 도구를 동시에 모두 호출해야 한다.

자연어 응답은 절대 작성하지 말고 오직 도구 호출(Tool Calls) 목록만 출력하라.
"""

ROUTER_SYSTEM_JSON = """\
You are a router. Read the user's input and determine all applicable agents. Output a JSON list containing one or more of the following agents:
- "agent1": If the user filters by registration status (e.g., '열려있는', '접수중', '신청 가능한').
- "agent2": If the user filters by schedule/date/time/range (e.g., '이번 달', '다음 주', '내일', '5월 20일', 'between dates').
- "agent3": If the user filters by specific topic/domain/interest (e.g., '네트워크', 'HTTP', 'DevOps', 'ML', '백엔드', 'DB', '디비', '기획', '디자인').

Example 1: "이번달 열려있는 네트워크 관련 강의 알려줘"
Output: {"agents": ["agent1", "agent2", "agent3"]}

Example 2: "다음 주 ML 강의 추천해줘"
Output: {"agents": ["agent2", "agent3"]}

Example 3: "5월 20일이랑 5월 23일 사이 기획 강의 있으면 알려줘"
Output: {"agents": ["agent1", "agent2", "agent3"]}

Respond strictly with JSON in the format: {"agents": ["agent1", "agent2", "agent3"]}.
"""

SYNTH_SYSTEM = """\
너는 SOMA 강의 복합 응답 합성기다. 여러 전문 에이전트가 순차적으로 조건을 필터링하여 최종 강의 목록을 추출했다.
사용자가 한 번에 읽기 좋은 한국어 응답으로 통합하라.

[규칙]
1. 반드시 컨텍스트로 제공되는 '최종 필터링된 강의 목록'에 존재하는 강의들만 안내하라.
2. [중요 UI 렌더링 규칙] 프론트엔드가 강의 목록을 전용 클릭 가능한 UI 카드로 자동 렌더링하므로, 텍스트 응답 내 일련번호나 강의 제목을 글머리 기호(-, *) 등으로 중복 나열하지 마라. 대신 자연스럽고 친절한 안내 멘트나 요약 문장(예: "요청하신 복합 조건에 부합하는 강의 목록입니다.")만 작성하라.
3. 내부 도구·에이전트 이름은 절대 노출하지 마라.
4. 통합 결과가 비어 있거나 최종 강의 목록이 없으면 "조건에 맞는 강의를 찾지 못했습니다."로 답한다.
"""


def _recent_history_messages(req: AgentRequest) -> List[dict]:
    # 후속 질의("그 중에 ML만", "다른 시간대는?")에서 라우터가 직전 컨텍스트를
    # 볼 수 있도록 최근 2턴(user/assistant 4개)만 전달.
    return [{"role": h.role, "content": h.content} for h in req.history[-4:]]


async def _route_with_tools(req: AgentRequest) -> List[str]:
    """tool-calling 라우팅. 빈 결과/미지원 시 JSON 폴백."""
    router_log.info("-> tool-calling LLM call (model=solar-pro3)")
    try:
        resp = await llm_call(
            timeout_s=10,
            max_attempts=3,
            model="solar-pro3",
            messages=[
                {"role": "system", "content": ROUTER_SYSTEM_TC},
                *_recent_history_messages(req),
                {"role": "user", "content": req.message},
            ],
            tools=LECTURE_TOOLS,
            tool_choice="auto",
        )
    except BadRequestError as e:
        router_log.warning("tool-calling rejected, falling back to JSON: %s", e)
        return await _route_via_json(req)
    except Exception as e:
        router_log.warning("tool-calling failed with %s: %s", type(e).__name__, e)
        return [DEFAULT_TOOL]

    msg = resp.choices[0].message
    tool_calls = getattr(msg, "tool_calls", None) or []
    if not tool_calls:
        router_log.warning("no tool_calls returned, falling back to JSON")
        return await _route_via_json(req)

    names: List[str] = []
    for tc in tool_calls:
        name = tc.function.name
        if name in TOOL_NAME_TO_AGENT:
            names.append(name)
        else:
            router_log.warning("unknown tool '%s' ignored", name)
    if not names:
        router_log.warning("all tool_calls unknown, defaulting to %s", DEFAULT_TOOL)
        return [DEFAULT_TOOL]
    router_log.info("selected tools: %s", names)
    return names


async def _route_via_json(req: AgentRequest) -> List[str]:
    router_log.info("-> JSON fallback LLM call (model=solar-pro3)")
    try:
        resp = await llm_call(
            timeout_s=10,
            max_attempts=2,
            model="solar-pro3",
            messages=[
                {"role": "system", "content": ROUTER_SYSTEM_JSON},
                *_recent_history_messages(req),
                {"role": "user", "content": req.message},
            ],
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
    except Exception as e:
        router_log.warning("JSON fallback LLM call failed with %s: %s", type(e).__name__, e)
        raw = "{}"

    router_log.info("JSON fallback raw: %s", raw)
    try:
        parsed = json.loads(raw)
        agents_list = parsed.get("agents")
        if isinstance(agents_list, list):
            tools = [LEGACY_TO_TOOL.get(a, DEFAULT_TOOL) for a in agents_list if a]
        else:
            legacy = parsed.get("agent")
            tools = [LEGACY_TO_TOOL.get(legacy, DEFAULT_TOOL)]
    except json.JSONDecodeError:
        tools = [DEFAULT_TOOL]

    tools = list(dict.fromkeys(tools))
    if not tools:
        tools = [DEFAULT_TOOL]
    router_log.info("JSON fallback selected: %s", tools)
    return tools


async def _run_one_agent(
    name: str, req: AgentRequest
) -> Tuple[str, Optional[AgentResult]]:
    handler = TOOL_NAME_TO_AGENT[name]
    try:
        result = await asyncio.wait_for(handler(req), timeout=AGENT_TIMEOUT_S)
        return name, result
    except asyncio.TimeoutError:
        log.warning("agent timed out: %s (>%.1fs)", name, AGENT_TIMEOUT_S)
        return name, None
    except Exception as e:
        log.exception("agent failed: %s (%s)", name, type(e).__name__)
        return name, None


async def _synthesize(
    req: AgentRequest, results: List[Tuple[str, AgentResult]], final_lectures: List[Lecture]
) -> str:
    synth_log.info("-> LLM call (model=solar-pro3, results=%d, final_lectures=%d)", len(results), len(final_lectures))
    
    intersected_text = "\n".join(
        f"- [{ '접수중' if l.is_open is True else '마감' if l.is_open is False else '상태미상' }] {l.title} ({l.dateStr} {l.timeRangeStr})"
        for l in final_lectures
    )
    
    agents_ctx = "\n\n".join(f"[결과 {i + 1}]\n{r.message}" for i, (_, r) in enumerate(results))
    ctx = f"각 에이전트 생성 결과:\n{agents_ctx}\n\n최종 필터링된 강의 목록 (이 강의들만 안내할 것):\n{intersected_text or '(없음)'}"
    
    try:
        resp = await llm_call(
            timeout_s=25,
            max_attempts=2,
            model="solar-pro3",
            messages=[
                {"role": "system", "content": SYNTH_SYSTEM},
                {"role": "system", "content": ctx},
                {"role": "user", "content": req.message},
            ],
        )
        text = resp.choices[0].message.content or ""
    except Exception as e:
        synth_log.warning("synthesis LLM call failed: %s", e)
        if final_lectures:
            text = f"요청하신 조건에 부합하는 강의 목록입니다. (총 {len(final_lectures)}건)"
        else:
            text = "조건에 맞는 강의를 찾지 못했습니다."

    synth_log.info("synthesized response (%d chars)", len(text))
    return text


async def run_gateway(req: AgentRequest) -> AgentResponse:
    """Tool-calling 라우터로 0~N개 에이전트를 호출하고 순차적으로 파이프라인 처리한다."""
    log.info(
        "received request | message=%r | history=%d | lectures=%d",
        req.message,
        len(req.history),
        len(req.lectures),
    )

    log.info("-> routing")
    tool_names = await _route_with_tools(req)
    if not tool_names:
        log.warning("router returned empty, defaulting to %s", DEFAULT_TOOL)
        tool_names = [DEFAULT_TOOL]

    # --- Rule-based Hybrid Enrichment (보완 파이프라인) ---
    msg_clean = req.message.lower().replace(" ", "")
    enriched_tools = []
    
    # 1. 접수 상태 키워드 검사
    if any(k in msg_clean for k in ["열려있는", "접수중", "신청가능", "오픈된", "모집중", "가능한"]):
        enriched_tools.append("list_open_lectures")
        
    # 2. 일정 키워드 검사
    if any(k in msg_clean for k in ["이번달", "다음주", "오늘", "내일", "이번주", "일정", "시간", "언제", "특강뭐", "특강있", "사이", "부터", "까지", "월", "일"]):
        enriched_tools.append("filter_lectures_by_schedule")
        
    # 3. 주제/관심사 키워드 검사
    interest_keywords = [
        "네트워크", "http", "devops", "ml", "ai", "백엔드", "프론트", "스프링", 
        "데이터", "클라우드", "보안", "앱", "웹", "파이썬", "자바", "리액트", 
        "딥러닝", "인공지능", "서버", "db", "sql", "인프라", "취업", "포트폴리오",
        "llm", "gpu", "api", "추천", "기획", "디자인", "ui/ux", "프로덕트"
    ]
    # '관련'과 같이 범용적인 단어는 단독으로 관심사 에이전트를 강제 덮어쓰지 않도록 세밀히 조건화
    if any(k in req.message.lower() for k in interest_keywords) or ("관련" in req.message.lower() and len(req.message) > 5):
        enriched_tools.append("recommend_lectures_by_interest")

    # LLM이 단일 기본 도구만 선택했으나 룰 기반으로 구체적 의도가 파악된 경우 결합 (일정이 누락되는 치명적 덮어쓰기 방지)
    if tool_names == [DEFAULT_TOOL] and enriched_tools:
        # 만약 enriched_tools에 agent3만 있고 질문에 날짜/일정 단어가 포함된 경우 agent2도 강제 보장
        if "recommend_lectures_by_interest" in enriched_tools and "filter_lectures_by_schedule" not in enriched_tools:
            if any(char.isdigit() or char in ["월", "일", "사이", "부터", "까지"] for char in msg_clean):
                enriched_tools.append("filter_lectures_by_schedule")
        tool_names = enriched_tools
    else:
        tool_names.extend(enriched_tools)

    # 중복 제거 및 순서 보존
    tool_names = list(dict.fromkeys(tool_names))
    if not tool_names:
        tool_names = [DEFAULT_TOOL]

    # 순차 파이프라인 실행을 위해 논리적 순서로 정렬 (agent1 -> agent2 -> agent3)
    order_map = {
        "list_open_lectures": 1,
        "filter_lectures_by_schedule": 2,
        "recommend_lectures_by_interest": 3,
    }
    tool_names.sort(key=lambda n: order_map.get(n, 99))

    log.info("-> dispatching sequentially to %s", tool_names)
    
    current_req = req
    successes: List[Tuple[str, AgentResult]] = []

    for name in tool_names:
        _, result = await _run_one_agent(name, current_req)
        if result is not None:
            successes.append((name, result))
            current_req = AgentRequest(
                message=req.message,
                history=req.history,
                lectures=result.lectures,
            )
            # 필터링 결과가 0건이 되면 이후 에이전트 실행 불필요하므로 조기 종료
            if not result.lectures:
                log.info("lectures filtered down to 0 by %s, breaking early", name)
                break
        else:
            log.warning("agent %s failed during sequential execution", name)

    if not successes:
        final_message = "강의 정보 처리에 실패했습니다."
        final_lectures: List[Lecture] = []
    elif len(successes) == 1:
        _, only = successes[0]
        final_message = only.message or ""
        final_lectures = list(only.lectures)
    else:
        # 마지막 성공한 에이전트의 강의 목록이 최종 결과
        final_lectures = list(successes[-1][1].lectures)
        final_message = await _synthesize(req, successes, final_lectures)

    new_history = list(req.history) + [
        HistoryMessage(role="user", content=req.message),
        HistoryMessage(role="assistant", content=final_message),
    ]

    log.info(
        "response ready | tools=%s | message_len=%d | lectures=%d",
        tool_names,
        len(final_message),
        len(final_lectures),
    )
    return AgentResponse(
        message=final_message,
        history=new_history,
        lectures=final_lectures,
        agent_used=tool_names,
    )
