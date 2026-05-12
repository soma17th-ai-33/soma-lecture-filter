import logging

from app.llm_client import get_client
from app.schemas import AgentRequest, AgentResult

log = logging.getLogger("agent3")

SYSTEM_PROMPT = """\
당신은 사용자의 관심사와 선호도를 분석하여 맞춤형 강의를 추천하는 AI 어시스턴트입니다.
사용자의 대화 기록과 현재 요청을 바탕으로 제공된 강의 목록 중 가장 적합한 강의들을 선택하여 추천해주세요.

[응답 규칙]
1. 분석 과정이나 판단 근거는 제외하고 친절하게 추천하는 메시지만 작성하세요.
2. 추천하는 강의가 있다면, 강의의 제목을 정확히 언급해주세요.
3. 강의 제목은 제공된 목록에 있는 텍스트와 완전히 동일하게 작성해야 합니다.
"""

async def agent3(req: AgentRequest) -> AgentResult:
    log.info("start | history=%d | lectures=%d", len(req.history), len(req.lectures))
    client = get_client()

    def _fmt(l):
        status = "접수중" if l.is_open is True else "마감" if l.is_open is False else "상태미상"
        return f"- [{status}] {l.title} ({l.dateStr} {l.timeRangeStr}, {l.author}) {l.url}"

    lectures_text = "\n".join(_fmt(l) for l in req.lectures)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"Available lectures:\n{lectures_text}"},
    ]
    for h in req.history:
        messages.append({"role": h.role, "content": h.content})
    messages.append({"role": "user", "content": req.message})

    log.info("-> LLM call (model=solar-pro3, messages=%d)", len(messages))
    resp = await client.chat.completions.create(
        model="solar-pro3",
        messages=messages
    )
    message_content = resp.choices[0].message.content or ""
    log.info("LLM response received (%d chars)", len(message_content))

    # ============================================================
    # 강의 필터링 로직 (agent2 스타일)
    # ============================================================
    filtered_lectures = [l for l in req.lectures if l.title in message_content]

    log.info("filtered lectures: %d", len(filtered_lectures))

    # 반환 계약: AgentResult(message=..., lectures=...) - 변경 금지
    return AgentResult(message=message_content, lectures=filtered_lectures)
