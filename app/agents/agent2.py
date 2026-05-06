import logging
from datetime import datetime

from app.llm_client import get_client
from app.schemas import AgentRequest, AgentResult

log = logging.getLogger("agent2")

# ============================================================
# 시스템 프롬프트 설정 (일정 기반 필터링 특화)
# ============================================================
SYSTEM_PROMPT = """\
너는 SOMA 멘토링 특강 필터링 전문가야. 
주어진 'Available lectures' 목록에서 사용자의 조건(날짜, 요일, 시간 등)에 맞는 강의를 찾아라.

[응답 규칙]
1. 분석 과정이나 판단 근거를 절대 설명하지 마라. (예: "확인해본 결과...", "이 강의는 ~해서 제외합니다" 등 금지)
2. 조건에 맞는 강의가 있다면, 오직 아래 포맷으로만 응답해라:
   - [강의 제목] (날짜 시간, 강사)
3. 조건에 맞는 강의가 하나도 없다면, 딱 한 문장만 출력해라:
   - "해당 시간대에는 신청 가능한 강의가 없습니다."
4. 제공된 목록에 없는 정보는 절대 지어내지 마라.
"""

async def agent2(req: AgentRequest) -> AgentResult:
    log.info("start | history=%d | lectures=%d", len(req.history), len(req.lectures))
    client = get_client()

    def _fmt(l):
        status = "접수중" if l.is_open is True else "마감" if l.is_open is False else "상태미상"
        
        # 💡 [추가된 로직] 파이썬 내장 기능으로 요일을 계산해서 LLM에게 떠먹여 줍니다.
        try:
            date_obj = datetime.strptime(l.dateStr, "%Y-%m-%d")
            weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][date_obj.weekday()]
            date_info = f"{l.dateStr}({weekday_kr})"
        except:
            date_info = l.dateStr # 혹시 날짜 형식이 이상하면 그냥 원본 출력
            
        return f"- [{status}] {l.title} ({date_info} {l.timeRangeStr}, {l.author}) {l.url}"

    lectures_text = "\n".join(_fmt(l) for l in req.lectures)

    # 일정 필터링의 핵심: LLM이 "내일", "다음 주" 등을 계산할 수 있도록 시스템 현재 시간 제공
    current_time_info = f"현재 기준 시간: {datetime.now().strftime('%Y-%m-%d %H:%M (%A)')}"

    # ============================================================
    # LLM 호출 메시지 구성
    # ============================================================
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"{current_time_info}\n\n현재 수강 가능한 강의 목록:\n{lectures_text}"},
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
    # 강의 필터링 로직
    # LLM이 조건에 맞다고 판단하여 응답에 언급한 강의 제목(l.title)만 추출하여 반환
    # (결과를 UI 챗봇 리스트 형태로 깔끔하게 렌더링하기 위함)
    # ============================================================
    filtered_lectures = [l for l in req.lectures if l.title in message]

    log.info("filtered lectures: %d", len(filtered_lectures))

    return AgentResult(message=message, lectures=filtered_lectures)