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
                "현재 '접수중'(is_open=True)인 강의만 골라 사용자에게 안내한다. "
                "사용자가 '지금 신청 가능한', '접수중', '오픈된' 강의를 묻거나 "
                "특별한 날짜/시간/관심사 조건 없이 등록 가능한 목록을 요청할 때 호출한다."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "filter_lectures_by_schedule",
            "description": (
                "특정 날짜·요일·시간대('내일', '다음 주', '저녁') 같은 일정 조건으로 "
                "강의를 필터링할 때 호출한다. 접수 상태와 무관하게 일정 기준 필터링이 핵심이면 "
                "이 도구를 쓴다."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recommend_lectures_by_interest",
            "description": (
                "관심사·직무·학습 목표(예: 'ML 관련', '백엔드 흥미 있어')에 맞는 강의를 "
                "추천할 때 호출한다. 단순 일정 조회가 아니라 주제·내용 기반 추천이 "
                "핵심일 때 사용한다."
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
너는 SOMA 강의 라우터다. 사용자 메시지를 읽고 적절한 도구를 호출하라.
- 단일 의도면 도구 1개만 호출.
- 복합 의도(일정+관심사 등)는 도구를 여러 개 호출해도 된다.
- 어떤 도구도 적합하지 않으면 list_open_lectures를 호출한다.
부가 자연어 응답 금지. 도구 호출만으로 응답하라.
"""

ROUTER_SYSTEM_JSON = """\
You are a router. Read the user's input and pick exactly one agent:
- agent1: Provides lecture schedule information, showing only lectures that are currently open for registration.
- agent2: Filters lectures based on date and time.
- agent3: Recommends and filters specific lectures based on the user's personal interests and preferences.
Respond with JSON only: {"agent": "agent1" | "agent2" | "agent3"}.
"""

SYNTH_SYSTEM = """\
너는 SOMA 강의 복합 응답 합성기다. 여러 전문 에이전트가 답변을 생성했고, 시스템이 이들의 교집합 조건을 만족하는 최종 강의 목록을 추출했다.
사용자가 한 번에 읽기 좋은 한국어 응답으로 통합하라.

[규칙]
1. 반드시 컨텍스트로 제공되는 '최종 필터링된 교집합 강의 목록'에 존재하는 강의들만 안내하라.
2. [중요 UI 렌더링 규칙] 프론트엔드가 강의 목록을 전용 클릭 가능한 UI 카드로 자동 렌더링하므로, 텍스트 응답 내에서 강의 제목을 글머리 기호(-, *) 등으로 중복 나열하지 마라. 대신 자연스럽고 친절한 안내 멘트나 요약 문장(예: "요청하신 복합 조건에 부합하는 강의 목록입니다.")만 작성하라.
3. 내부 도구·에이전트 이름은 절대 노출하지 마라.
4. 통합 결과가 비어 있거나 교집합 강의 목록이 없으면 "조건에 맞는 강의를 찾지 못했습니다."로 답한다.
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
    router_log.info("JSON fallback raw: %s", raw)
    try:
        parsed = json.loads(raw)
        legacy = parsed.get("agent")
    except json.JSONDecodeError:
        legacy = None
    tool = LEGACY_TO_TOOL.get(legacy, DEFAULT_TOOL)
    router_log.info("JSON fallback selected: %s", tool)
    return [tool]


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


def _intersect_lectures(groups: List[List[Lecture]]) -> List[Lecture]:
    if not groups:
        return []
    base = groups[0]
    out: List[Lecture] = []
    for lec in base:
        in_all = True
        for other_group in groups[1:]:
            if not any(other_lec.url == lec.url for other_lec in other_group):
                in_all = False
                break
        if in_all:
            out.append(lec)
    return out


async def _synthesize(
    req: AgentRequest, results: List[Tuple[str, AgentResult]], final_lectures: List[Lecture]
) -> str:
    synth_log.info("-> LLM call (model=solar-pro3, results=%d, final_lectures=%d)", len(results), len(final_lectures))
    
    intersected_text = "\n".join(
        f"- [{ '접수중' if l.is_open is True else '마감' if l.is_open is False else '상태미상' }] {l.title} ({l.dateStr} {l.timeRangeStr})"
        for l in final_lectures
    )
    
    agents_ctx = "\n\n".join(f"[결과 {i + 1}]\n{r.message}" for i, (_, r) in enumerate(results))
    ctx = f"각 에이전트 생성 결과:\n{agents_ctx}\n\n최종 필터링된 교집합 강의 목록 (이 강의들만 안내할 것):\n{intersected_text or '(없음)'}"
    
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
    synth_log.info("synthesized response (%d chars)", len(text))
    return text


async def run_gateway(req: AgentRequest) -> AgentResponse:
    """Tool-calling 라우터로 0~N개 에이전트를 호출하고 결과를 합성한다."""
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
    tool_names = list(dict.fromkeys(tool_names))

    log.info("-> dispatching to %s", tool_names)
    raw_results = await asyncio.gather(
        *(_run_one_agent(n, req) for n in tool_names)
    )
    successes: List[Tuple[str, AgentResult]] = [
        (n, r) for n, r in raw_results if r is not None
    ]

    if not successes:
        final_message = "강의 정보 처리에 실패했습니다."
        final_lectures: List[Lecture] = []
    elif len(successes) == 1:
        _, only = successes[0]
        final_message = only.message or ""
        final_lectures = list(only.lectures)
    else:
        final_lectures = _intersect_lectures([r.lectures for _, r in successes])
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
