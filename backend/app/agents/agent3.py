import logging

from app.agents._common import parse_index_response
from app.llm_client import get_client
from app.schemas import AgentRequest, AgentResult

log = logging.getLogger("agent3")

SYSTEM_PROMPT = """\
당신은 사용자의 관심사와 선호도를 분석하여 맞춤형 강의를 추천하는 AI 어시스턴트입니다.
'Available lectures' 목록은 각 줄 앞에 [#N] 번호가 붙어 있습니다. 사용자의 관심사에 가장 부합하는 강의의 번호 N을 indices 배열에 담으세요.

[응답 형식]
반드시 다음 JSON 한 객체로만 응답하세요. 다른 텍스트(설명/판단 근거/마크다운) 금지.
{
  "message": "사용자에게 보낼 친절한 한국어 추천 멘트. 강의 제목을 글머리 기호로 나열하지 마세요(프론트가 카드로 따로 렌더링).",
  "indices": [1, 3, 5]
}

[규칙]
1. indices에는 'Available lectures' 항목 앞의 [#N] 안 숫자만 정수로 담으세요.
2. 추천할 강의가 없으면 indices는 빈 배열([]).
3. 목록에 없는 번호를 절대 만들지 마세요.
"""


async def agent3(req: AgentRequest) -> AgentResult:
    log.info("start | history=%d | lectures=%d", len(req.history), len(req.lectures))
    client = get_client()

    def _fmt(i, l):
        status = "접수중" if l.is_open is True else "마감" if l.is_open is False else "상태미상"
        return f"[#{i}] [{status}] {l.title} ({l.dateStr} {l.timeRangeStr}, {l.author})"

    lectures_text = "\n".join(_fmt(i + 1, l) for i, l in enumerate(req.lectures))

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"Available lectures:\n{lectures_text}"},
    ]
    for h in req.history[-4:]:
        messages.append({"role": h.role, "content": h.content})
    messages.append({"role": "user", "content": req.message})

    log.info("-> LLM call (model=solar-pro3, messages=%d)", len(messages))
    resp = await client.chat.completions.create(
        model="solar-pro3",
        messages=messages,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or ""
    log.info("LLM response received (%d chars): %s", len(raw), raw)

    message, filtered_lectures = parse_index_response(raw, req.lectures)
    return AgentResult(message=message, lectures=filtered_lectures)
