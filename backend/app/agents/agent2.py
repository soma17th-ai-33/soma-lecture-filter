import json
import logging
import re
from datetime import datetime
from typing import Optional

from app.llm_client import llm_call
from app.schemas import AgentRequest, AgentResult

log = logging.getLogger("agent2")

# ============================================================
# 시스템 프롬프트 설정 (일정 파라미터 추출용 툴 호출 특화)
# ============================================================
SYSTEM_PROMPT = """\
너는 SOMA 멘토링 특강 일정 필터링 어시스턴트다.
사용자의 질문과 대화 기록을 분석하여, 사용자가 찾고자 하는 일정 조건(특정 날짜, 요일, 시작/종료 시간대)을 파악하고 `filter_schedule` 도구를 호출하라.

[중요 지침]
1. '오늘', '내일', '이번 주' 등의 상대적 날짜 표현을 해석할 때, 함께 제공되는 '현재 강의 목록에 존재하는 날짜들'을 적극 참고하여 실제 데이터가 존재하는 날짜를 target_dates로 매핑하라. (예: 새벽/자정 직후 질의 시, 사용자가 의도한 '오늘'이 강의 목록에 존재하는 전날 날짜일 수 있음)
2. 자연어 응답은 작성하지 말고 오직 도구 호출만 수행하라.
"""

AGENT2_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "filter_schedule",
            "description": "요청된 날짜, 요일, 시간대 조건으로 강의를 필터링하기 위해 호출한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_dates": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "필터링할 대상 날짜 목록 (YYYY-MM-DD 형식). 예: ['2026-05-13']",
                    },
                    "day_of_week": {
                        "type": "string",
                        "description": "특정 요일 조건 (월, 화, 수, 목, 금, 토, 일 중 하나). 없으면 생략.",
                    },
                    "start_hour": {
                        "type": "integer",
                        "description": "시작 시간 필터 조건 (0~23). 이 시간 이후에 시작하는 강의를 찾을 때 지정. 없으면 생략.",
                    },
                    "end_hour": {
                        "type": "integer",
                        "description": "종료 시간 필터 조건 (0~23). 이 시간 이전에 시작하는 강의를 찾을 때 지정. 없으면 생략.",
                    },
                },
                "additionalProperties": False,
            },
        },
    }
]


def _get_start_hour(time_str: str) -> Optional[int]:
    # 예: "19:00~21:00" -> 19
    match = re.search(r"(\d+):", time_str)
    if match:
        return int(match.group(1))
    return None


async def agent2(req: AgentRequest) -> AgentResult:
    log.info("start | history=%d | lectures=%d", len(req.history), len(req.lectures))

    # 디버깅 파이프라인 및 컨텍스트 강화: 현재 목록에 존재하는 고유 날짜들 추출
    unique_dates = sorted(list(set(l.dateStr.strip() for l in req.lectures if l.dateStr)))
    log.info("debugging pipeline | available unique dates in req.lectures: %s", unique_dates)

    current_time_info = (
        f"현재 기준 시간: {datetime.now().strftime('%Y-%m-%d %H:%M (%A)')}\n"
        f"현재 강의 목록에 존재하는 날짜들: {unique_dates}"
    )

    messages = [
        {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{current_time_info}"},
    ]
    for h in req.history:
        messages.append({"role": h.role, "content": h.content})
    messages.append({"role": "user", "content": req.message})

    log.info("-> LLM tool call (model=solar-pro3)")
    try:
        resp = await llm_call(
            model="solar-pro3",
            messages=messages,
            tools=AGENT2_TOOLS,
            tool_choice={"type": "function", "function": {"name": "filter_schedule"}},
        )
        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []
    except Exception as e:
        log.exception("LLM tool call failed: %s", e)
        tool_calls = []

    target_dates = []
    day_of_week = None
    start_hour = None
    end_hour = None

    if tool_calls:
        tc = tool_calls[0]
        try:
            args = json.loads(tc.function.arguments)
            raw_dates = args.get("target_dates") or []
            target_dates = []
            for d in raw_dates:
                if d:
                    m = re.match(r"^(\d{4}-\d{2}-\d{2})", d.strip())
                    target_dates.append(m.group(1) if m else d.strip())
            day_of_week = args.get("day_of_week")
            start_hour = args.get("start_hour")
            end_hour = args.get("end_hour")
            log.info("parsed tool arguments: %s", args)
        except Exception as e:
            log.warning("failed to parse tool arguments: %s", e)

    # ============================================================
    # 결정론적 알고리즘 필터링 및 디버깅 통계 파이프라인
    # ============================================================
    filtered_lectures = []
    debug_stats = {
        "total": len(req.lectures),
        "excluded_by_date": 0,
        "excluded_by_weekday": 0,
        "excluded_by_hour": 0,
    }

    for l in req.lectures:
        clean_date = l.dateStr.strip() if l.dateStr else ""
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", clean_date)
        pure_date = m.group(1) if m else clean_date
        
        # 1. target_dates 조건
        if target_dates and pure_date not in target_dates:
            debug_stats["excluded_by_date"] += 1
            continue

        # 2. day_of_week 조건
        if day_of_week:
            try:
                date_obj = datetime.strptime(pure_date, "%Y-%m-%d")
                weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][date_obj.weekday()]
                if day_of_week not in weekday_kr:
                    debug_stats["excluded_by_weekday"] += 1
                    continue
            except:
                pass

        # 3. start_hour / end_hour 조건
        lec_start = _get_start_hour(l.timeRangeStr)
        if lec_start is not None:
            if start_hour is not None and lec_start < start_hour:
                debug_stats["excluded_by_hour"] += 1
                continue
            if end_hour is not None and lec_start > end_hour:
                debug_stats["excluded_by_hour"] += 1
                continue

        filtered_lectures.append(l)

    log.info("debugging pipeline | filtering stats: %s", debug_stats)

    # ============================================================
    # 사용자 노출용 친절한 템플릿 메시지 생성 (툴 호출 내역 은닉)
    # ============================================================
    if filtered_lectures:
        message = f"요청하신 일정 조건에 맞는 강의 목록입니다. (총 {len(filtered_lectures)}건)"
    else:
        message = "해당 일정에는 수강 가능한 강의가 없습니다."

    log.info("filtered lectures: %d", len(filtered_lectures))
    return AgentResult(message=message, lectures=filtered_lectures)