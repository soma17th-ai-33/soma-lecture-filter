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
사용자의 현재 요청을 분석하여 찾고자 하는 구체적인 일정 조건(특정 날짜, 요일, 시작/종료 시간대)을 파악하고 `filter_schedule` 도구를 호출하라.

[중요 지침]
1. 'X월 Y일부터 A월 B일 사이'와 같이 특정 기간/범위를 검색하는 요청인 경우, 시작 날짜를 start_date로, 종료 날짜를 end_date로 지정하라 (YYYY-MM-DD 형식).
2. '오늘', '내일', '특정 일자 하루(예: 5월 20일)' 등 단일 날짜를 검색하는 경우에는 start_date와 end_date를 동일하게 해당 일자로 지정하라.
3. 사용자의 현재 요청에 구체적인 날짜/요일/시간 조건이 명시되어 있지 않고 단순히 '이번 달'과 같이 넓은 범위만 요청한 경우, 특정 날짜나 요일, 시간대를 절대 임의로 유추하거나 좁혀서 추출하지 마라. 조건이 구체적이지 않으면 파라미터를 빈 객체({})로 전달하라.
4. 자연어 응답은 작성하지 말고 오직 도구 호출만 수행하라.
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
                    "start_date": {
                        "type": "string",
                        "description": "검색 기간의 시작 날짜 (YYYY-MM-DD 형식). 특정 날짜 하루만 검색할 경우 start_date와 end_date를 동일하게 지정.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "검색 기간의 종료 날짜 (YYYY-MM-DD 형식). 특정 날짜 하루만 검색할 경우 start_date와 end_date를 동일하게 지정.",
                    },
                    "days_of_week": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "사용자가 질문에서 특정 요일들을 명시적으로 언급하고 요청한 경우에만 입력 (예: ['월', '수', '금']). 언급이 없으면 절대 임의로 채우지 말고 생략.",
                    },
                    "start_hour": {
                        "type": "integer",
                        "description": "시작 시간 조건 (0~23). 특정 시간 이후 시작을 명시적으로 요청한 경우에만 지정. 생략 가능.",
                    },
                    "end_hour": {
                        "type": "integer",
                        "description": "종료 시간 조건 (0~23). 특정 시간 이전 시작을 명시적으로 요청한 경우에만 지정. 생략 가능.",
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
        {"role": "user", "content": req.message},
    ]

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

    start_date = None
    end_date = None
    days_of_week = []
    start_hour = None
    end_hour = None

    if tool_calls:
        tc = tool_calls[0]
        try:
            args = json.loads(tc.function.arguments)
            
            def _clean_d(val):
                if val and isinstance(val, str):
                    m = re.match(r"^(\d{4}-\d{2}-\d{2})", val.strip())
                    return m.group(1) if m else val.strip()
                return None

            start_date = _clean_d(args.get("start_date"))
            end_date = _clean_d(args.get("end_date"))
            
            # [단일 파라미터 자동 치유] 한쪽 날짜만 추출된 경우나 사이/부터/까지 키워드가 존재할 때 안전하게 범위 완성
            if unique_dates:
                if start_date and not end_date:
                    end_date = unique_dates[-1]
                elif end_date and not start_date:
                    start_date = unique_dates[0]
                elif not start_date and not end_date and any(w in req.message for w in ["사이", "부터", "까지"]):
                    start_date = unique_dates[0]
                    end_date = unique_dates[-1]
                    
            days_of_week = args.get("days_of_week") or []
            if isinstance(days_of_week, str):
                days_of_week = [days_of_week]
            
            valid_days = []
            for d in days_of_week:
                if d:
                    d_str = d.strip()
                    # 환각 방어: 질의에 해당 요일 글자나 요일 관련 키워드가 전혀 없으면 무시
                    if d_str in req.message or any(w in req.message for w in ["주말", "평일", "요일"]):
                        valid_days.append(d_str)
                    else:
                        log.info("ignoring hallucinated days_of_week item '%s' not found in user message", d_str)
            days_of_week = valid_days

            start_hour = args.get("start_hour")
            if start_hour == 0:
                start_hour = None
            end_hour = args.get("end_hour")
            if end_hour == 0:
                end_hour = None

            log.info("parsed tool arguments (after validation): start_date=%s, end_date=%s, days_of_week=%s, start_hour=%s, end_hour=%s", start_date, end_date, days_of_week, start_hour, end_hour)
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
        
        # 1. 날짜 기간 조건 (start_date / end_date)
        if start_date and pure_date < start_date:
            debug_stats["excluded_by_date"] += 1
            log.info("excluded by start_date (start=%s, lec_date=%s): %s", start_date, pure_date, l.title)
            continue
        if end_date and pure_date > end_date:
            debug_stats["excluded_by_date"] += 1
            log.info("excluded by end_date (end=%s, lec_date=%s): %s", end_date, pure_date, l.title)
            continue

        # 2. days_of_week 조건
        if days_of_week:
            try:
                date_obj = datetime.strptime(pure_date, "%Y-%m-%d")
                weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][date_obj.weekday()]
                if not any(d in weekday_kr for d in days_of_week):
                    debug_stats["excluded_by_weekday"] += 1
                    log.info("excluded by days_of_week (target=%s, lec_day=%s): %s", days_of_week, weekday_kr, l.title)
                    continue
            except:
                pass

        # 3. start_hour / end_hour 조건
        lec_start = _get_start_hour(l.timeRangeStr)
        if lec_start is not None:
            if start_hour is not None and lec_start < start_hour:
                debug_stats["excluded_by_hour"] += 1
                log.info("excluded by start_hour (target>=%s, lec_start=%s): %s", start_hour, lec_start, l.title)
                continue
            if end_hour is not None and lec_start > end_hour:
                debug_stats["excluded_by_hour"] += 1
                log.info("excluded by end_hour (target<=%s, lec_start=%s): %s", end_hour, lec_start, l.title)
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