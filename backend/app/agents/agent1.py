import logging

from app.schemas import AgentRequest, AgentResult

log = logging.getLogger("agent1")


async def agent1(req: AgentRequest) -> AgentResult:
    log.info("start | history=%d | lectures=%d", len(req.history), len(req.lectures))

    # ============================================================
    # 강의 필터링 - 접수중(is_open=True)인 강의만 결정론적(Algorithmic) 필터링
    # ============================================================
    filtered_lectures = [l for l in req.lectures if l.is_open is True]

    if filtered_lectures:
        message = f"현재 접수중인 강의 목록입니다. (총 {len(filtered_lectures)}건)"
    else:
        message = "현재 접수중인 강의가 없습니다."

    log.info("filtered lectures: %d", len(filtered_lectures))

    # 반환 계약: AgentResult(message=..., lectures=...) - 변경 금지
    return AgentResult(message=message, lectures=filtered_lectures)
