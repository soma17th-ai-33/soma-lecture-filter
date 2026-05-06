import logging

from app.llm_client import get_client
from app.schemas import AgentRequest, AgentResult

log = logging.getLogger("agent2")

# ============================================================
# TODO [agent2 담당자 작성] - 시스템 프롬프트
# 이 agent의 역할/지시사항을 작성하세요.
# ============================================================
SYSTEM_PROMPT = "AGENT2 system prompt placeholder"


async def agent2(req: AgentRequest) -> AgentResult:
    log.info("start | history=%d | lectures=%d", len(req.history), len(req.lectures))
    client = get_client()

    def _fmt(l):
        status = "접수중" if l.is_open is True else "마감" if l.is_open is False else "상태미상"
        return f"- [{status}] {l.title} ({l.dateStr} {l.timeRangeStr}, {l.author}) {l.url}"

    lectures_text = "\n".join(_fmt(l) for l in req.lectures)

    # ============================================================
    # TODO [agent2 담당자 작성] - LLM 호출 메시지 구성
    # 필요 시 messages 구조/포함 정보를 변경하세요.
    # ============================================================
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"Available lectures:\n{lectures_text}"},
    ]
    for h in req.history:
        messages.append({"role": h.role, "content": h.content})

    log.info("-> LLM call (model=solar-pro3, messages=%d)", len(messages))
    resp = await client.chat.completions.create(
        model="solar-pro3",
        messages=messages,
    )
    message = resp.choices[0].message.content or ""
    log.info("LLM response received (%d chars)", len(message))

    # ============================================================
    # TODO [agent2 담당자 작성] - 강의 필터링 로직
    # req.lectures 중 이 agent 기준에 맞는 강의만 골라 반환하세요.
    # 필터링이 필요 없으면 빈 리스트([]) 그대로 반환.
    # ============================================================
    filtered_lectures = []

    log.info("filtered lectures: %d", len(filtered_lectures))

    # 반환 계약: AgentResult(message=..., lectures=...) - 변경 금지
    return AgentResult(message=message, lectures=filtered_lectures)
