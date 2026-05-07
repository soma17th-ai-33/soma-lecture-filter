import logging

from app.llm_client import get_client
from app.schemas import AgentRequest, AgentResult

log = logging.getLogger("agent1")

# ============================================================
# 시스템 프롬프트 - 접수중인 강의 일정 안내
# ============================================================
SYSTEM_PROMPT = """\
너는 소프트웨어 마에스트로(SWM) 강의 일정 안내 전문가야.
사용자가 강의 일정을 물어보면, 현재 접수중인 강의만 골라서 친절하게 안내해줘.
접수가 마감되었거나 상태를 알 수 없는 강의는 안내하지 마.
강의 제목, 날짜, 시간, 강사 정보를 포함해서 알려주고, 접수 링크도 함께 안내해줘.
접수중인 강의가 없으면 현재 접수중인 강의가 없다고 안내해줘.
"""


async def agent1(req: AgentRequest) -> AgentResult:
    log.info("start | history=%d | lectures=%d", len(req.history), len(req.lectures))
    client = get_client()

    def _fmt(l):
        status = "접수중" if l.is_open is True else "마감" if l.is_open is False else "상태미상"
        return f"- [{status}] {l.title} ({l.dateStr} {l.timeRangeStr}, {l.author}) {l.url}"

    lectures_text = "\n".join(_fmt(l) for l in req.lectures)

    # ============================================================
    # LLM 호출 메시지 구성 - 접수중인 강의만 전달
    # ============================================================
    open_lectures_text = "\n".join(_fmt(l) for l in req.lectures if l.is_open is True)
    if not open_lectures_text:
        open_lectures_text = "(현재 접수중인 강의가 없습니다)"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"현재 접수중인 강의 목록:\n{open_lectures_text}"},
    ]
    for h in req.history:
        messages.append({"role": h.role, "content": h.content})
    messages.append({"role": "user", "content": req.message})

    log.info("-> LLM call (model=solar-pro3, messages=%d)", len(messages))
    resp = await client.chat.completions.create(
        model="solar-pro3",
        messages=messages,
    )
    message = resp.choices[0].message.content or ""
    log.info("LLM response received (%d chars)", len(message))

    # ============================================================
    # 강의 필터링 - 접수중(is_open=True)인 강의만 반환
    # ============================================================
    filtered_lectures = [l for l in req.lectures if l.is_open is True]

    log.info("filtered lectures: %d", len(filtered_lectures))

    # 반환 계약: AgentResult(message=..., lectures=...) - 변경 금지
    return AgentResult(message=message, lectures=filtered_lectures)
