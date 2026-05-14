import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from app.agents._common import parse_index_response
from app.llm_client import get_client
from app.schemas import AgentRequest, AgentResult

log = logging.getLogger("agent2")

_DATE_RE = re.compile(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})")
_WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def _parse_date(date_str: str) -> Optional[datetime]:
    """다양한 dateStr 포맷('YYYY-MM-DD', 'YYYY.MM.DD', 'YYYY/MM/DD', 'YYYY년 MM월 DD일' 등)에서 날짜 추출."""
    if not date_str:
        return None
    m = _DATE_RE.search(date_str)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _build_system_prompt(now: datetime) -> str:
    today_iso = now.strftime("%Y-%m-%d")
    tomorrow_iso = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    day_after_iso = (now + timedelta(days=2)).strftime("%Y-%m-%d")
    weekday_kr = _WEEKDAY_KR[now.weekday()]

    return f"""\
너는 SOMA 멘토링 특강 일정 필터링 전문가다.

[현재 시각]
- 오늘: {today_iso} ({weekday_kr}요일)
- 내일: {tomorrow_iso}
- 모레: {day_after_iso}

[강의 목록 포맷]
'Available lectures' 각 줄은 다음 형식이다:
[#N] [상태] 제목 (YYYY-MM-DD(요일, N월 N일) 시간범위, 강사)
예: [#3] [접수중] 백엔드 입문 (2026-05-15(금, 5월 15일) 19:00 ~ 21:00, 홍길동)

[날짜 매칭 가이드]
- 사용자 표현을 위 라벨의 'YYYY-MM-DD' 또는 'N월 N일'과 직접 비교하라.
- 절대 날짜('5월 15일', '5/15', '15일') → 오늘({today_iso}) 기준 가까운 미래의 같은 월·일.
  예: '5월 15일' → 2026-05-15(라벨에 '5월 15일'이 포함된 항목).
- 상대 표현 → 오늘 기준 계산:
  '오늘' = {today_iso} / '내일' = {tomorrow_iso} / '모레' = {day_after_iso}
  '이번 주 X요일', '다음 주', '이번 주말' 등도 오늘 기준.
- 시간대 키워드는 timeRangeStr 시작 시각 기준:
  '오전' 06:00~12:00 / '점심' 12:00~14:00 / '오후' 12:00~18:00 / '저녁' 18:00~22:00 / '밤' 22:00 이후
- '주말' = 토·일, '평일' = 월~금.
- 날짜만 지정되고 시간대 단서가 없으면 그 날짜의 모든 강의를 포함.
- 날짜·시간이 일치하지 않는 강의는 indices에 절대 넣지 마라.

[매칭 절차]
1. 사용자 메시지에서 날짜·시간 조건을 추출하라.
2. 추출한 날짜를 'YYYY-MM-DD' 형태로 환산하라.
3. 'Available lectures' 각 줄을 훑어 라벨의 'YYYY-MM-DD'가 정확히 일치하는 항목을 모두 찾아라.
4. 시간대 조건이 있다면 timeRangeStr로 추가 필터링.
5. 찾은 항목의 [#N]을 indices에 담아라.

[응답 형식]
반드시 다음 JSON 한 객체로만 응답하라. 다른 텍스트(설명/판단 근거/마크다운/코드펜스) 금지.
{{
  "message": "사용자에게 보낼 한국어 안내 멘트. 강의 제목을 글머리 기호로 나열하지 마라(프론트가 카드로 따로 렌더링). 조건에 맞는 강의가 없으면 '해당 시간대에는 신청 가능한 강의가 없습니다.' 한 문장만.",
  "indices": [1, 3, 5]
}}

[규칙]
1. indices에는 'Available lectures' 항목 앞의 [#N] 안 숫자만 정수로 담아라.
2. 조건에 맞는 강의가 하나도 없으면 indices는 빈 배열([]).
3. 목록에 없는 번호를 절대 만들지 마라.
"""


async def agent2(req: AgentRequest) -> AgentResult:
    log.info("start | history=%d | lectures=%d", len(req.history), len(req.lectures))
    client = get_client()
    now = datetime.now()

    def _fmt(i, l):
        status = "접수중" if l.is_open is True else "마감" if l.is_open is False else "상태미상"
        parsed = _parse_date(l.dateStr)
        if parsed:
            weekday_kr = _WEEKDAY_KR[parsed.weekday()]
            date_info = f"{parsed.strftime('%Y-%m-%d')}({weekday_kr}, {parsed.month}월 {parsed.day}일)"
        else:
            date_info = l.dateStr or "(날짜미상)"
        return f"[#{i}] [{status}] {l.title} ({date_info} {l.timeRangeStr}, {l.author})"

    lectures_text = "\n".join(_fmt(i + 1, l) for i, l in enumerate(req.lectures))

    messages = [
        {"role": "system", "content": _build_system_prompt(now)},
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
