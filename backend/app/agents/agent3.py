import logging
import json

from app.llm_client import get_client
from app.schemas import AgentRequest, AgentResult

log = logging.getLogger("agent3")

# ============================================================
# TODO [agent3 담당자 작성] - 시스템 프롬프트
# 이 agent의 역할/지시사항을 작성하세요.
# ============================================================
SYSTEM_PROMPT = """\
당신은 사용자의 관심사와 선호도를 분석하여 맞춤형 강의를 추천하는 AI 어시스턴트입니다.
사용자의 대화 기록과 현재 요청을 바탕으로 제공된 강의 목록 중 가장 적합한 강의들을 선택하세요.
반드시 아래의 JSON 형식으로 응답해야 합니다.
{
  "message": "사용자에게 전달할 추천 이유나 친절한 메시지",
  "selected_urls": ["선택한 강의의 url 1", "선택한 강의의 url 2"]
}
"""


async def agent3(req: AgentRequest) -> AgentResult:
    log.info("start | history=%d | lectures=%d", len(req.history), len(req.lectures))
    client = get_client()

    def _fmt(l):
        status = "접수중" if l.is_open is True else "마감" if l.is_open is False else "상태미상"
        return f"- [{status}] {l.title} ({l.dateStr} {l.timeRangeStr}, {l.author}) {l.url}"

    lectures_text = "\n".join(_fmt(l) for l in req.lectures)

    # ============================================================
    # TODO [agent3 담당자 작성] - LLM 호출 메시지 구성
    # 필요 시 messages 구조/포함 정보를 변경하세요.
    # ============================================================
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
        messages=messages,
        response_format={"type": "json_object"}
    )
    message_content = resp.choices[0].message.content or "{}"
    log.info("LLM response received (%d chars)", len(message_content))

    # ============================================================
    # TODO [agent3 담당자 작성] - 강의 필터링 로직
    # req.lectures 중 이 agent 기준에 맞는 강의만 골라 반환하세요.
    # 필터링이 필요 없으면 빈 리스트([]) 그대로 반환.
    # ============================================================
    final_message = "강의를 추천해 드립니다."
    filtered_lectures = []
    
    try:
        llm_data = json.loads(message_content)
        final_message = llm_data.get("message", final_message)
        selected_urls = set(llm_data.get("selected_urls", []))
        
        filtered_lectures = [
            lecture for lecture in req.lectures 
            if lecture.url in selected_urls
        ]
    except json.JSONDecodeError:
        log.error("LLM did not return valid JSON: %s", message_content)
        final_message = message_content

    log.info("filtered lectures: %d", len(filtered_lectures))

    # 반환 계약: AgentResult(message=..., lectures=...) - 변경 금지
    return AgentResult(message=final_message, lectures=filtered_lectures)
