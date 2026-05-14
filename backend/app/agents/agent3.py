import asyncio
import json
import logging
from typing import List

from app.llm_client import llm_call
from app.schemas import AgentRequest, AgentResult, Lecture

log = logging.getLogger("agent3")

SYSTEM_PROMPT = """\
너는 SOMA 멘토링 특강 맞춤형 주제 필터링 어시스턴트다.
사용자의 현재 요청을 분석하여, 함께 제공되는 강의 목록에서 해당 주제/도메인/기술 키워드와 연관성이 조금이라도 있는 강의들을 빠짐없이 찾아 선별해 `select_recommended_lectures` 도구를 호출하라.

[선별 지침]
1. 오직 사용자가 요청한 기술 스택이나 주제/도메인과의 연관성만 평가하여 부합하는 강의를 적극적으로 선별하라. 사용자 질문에 특정 날짜, 요일, 시간대 등의 일정 조건이나 접수 상태 조건이 함께 포함되어 있더라도, 너는 일정/상태 필터링을 절대 수행하지 마라(다른 전문 에이전트가 담당함). 오직 주제가 부합하는지만 판단하라.
2. 사용자가 '디비', '백엔드', '프론트엔드', '기획', '디자인' 등의 줄임말이나 포괄적 기술/도메인을 요청한 경우, 제목에 해당 단어가 직접 없더라도 연관된 구체적 주제(예: 디비 -> DB, SQL, 데이터베이스 / 백엔드 -> 서버, API, 스프링부트 / 기획 -> 프로덕트 기획, 기획서 리뷰, 아이디어 검증, 사용자 조사, MVP 기획 등)를 다루는 강의라면 지능적으로 파악하여 모두 포함하라.
3. 사용자의 관심사나 도메인과 부합하는 강의들을 최대한 빠짐없이 모두 찾아내어 selected_ids에 포함시켜야 한다. 단, 도메인이 완전히 무관한 강의만 제외하라.
4. 부합하는 강의가 하나도 없으면 selected_ids를 빈 배열([])로 반환하라.
5. 오직 선별된 강의의 ID 목록만 반환하며, 추가 설명은 작성하지 마라.
"""

AGENT3_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "select_recommended_lectures",
            "description": "사용자의 관심사나 요청에 부합하는 강의들을 선택하기 위해 호출한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selected_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "추천/선택된 강의들의 고유 ID 번호 목록. 예: [3, 12, 45]",
                    }
                },
                "additionalProperties": False,
            },
        },
    }
]


async def agent3(req: AgentRequest) -> AgentResult:
    log.info("start | history=%d | lectures=%d", len(req.history), len(req.lectures))
    
    # 전달받은 강의 목록 상세 로깅 (디버깅 지원)
    for idx, l in enumerate(req.lectures, 1):
        log.info("incoming candidate [%d/%d]: %s (%s)", idx, len(req.lectures), l.title, l.dateStr)

    if not req.lectures:
        return AgentResult(message="관심사에 부합하는 강의를 찾지 못했습니다.", lectures=[])

    # 청크 크기 설정 (LLM이 한 번에 완벽하게 집중하여 스캔할 수 있는 최적의 크기: 40건)
    chunk_size = 40
    lecture_chunks = [req.lectures[i:i + chunk_size] for i in range(0, len(req.lectures), chunk_size)]

    log.info("divided %d lectures into %d chunks for parallel processing", len(req.lectures), len(lecture_chunks))

    async def _process_chunk(chunk_lecs: List[Lecture]) -> List[Lecture]:
        # LLM의 상대적 1-based 인덱싱 편향을 원천 차단하기 위해, 청크마다 로컬 1번부터 매핑 생성
        local_map = {idx: l for idx, l in enumerate(chunk_lecs, 1)}

        def _fmt(idx, l):
            status = "접수중" if l.is_open is True else "마감" if l.is_open is False else "상태미상"
            return f"[ID: {idx}] [{status}] {l.title} ({l.author})"

        lectures_text = "\n".join(_fmt(idx, l) for idx, l in local_map.items())

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": f"Available lectures in this batch:\n{lectures_text}"},
            {"role": "user", "content": req.message},
        ]

        try:
            resp = await llm_call(
                model="solar-pro3",
                messages=messages,
                tools=AGENT3_TOOLS,
                tool_choice={"type": "function", "function": {"name": "select_recommended_lectures"}},
            )
            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None) or []
            if tool_calls:
                args = json.loads(tool_calls[0].function.arguments)
                selected_ids = args.get("selected_ids") or []
                selected_lecs = [local_map[i] for i in selected_ids if i in local_map]
                log.info("chunk LLM selected %d lectures: %s", len(selected_lecs), [l.title for l in selected_lecs])
                return selected_lecs
        except Exception as e:
            log.warning("chunk LLM call failed: %s", e)
        return []

    # 복수의 청크를 비동기 병렬 처리
    chunk_results = await asyncio.gather(*[_process_chunk(c) for c in lecture_chunks])
    
    filtered_lectures: List[Lecture] = []
    seen_titles = set()
    for lecs in chunk_results:
        for lec in lecs:
            if lec.title not in seen_titles:
                seen_titles.add(lec.title)
                filtered_lectures.append(lec)

    log.info("filtered lectures: %d", len(filtered_lectures))

    if filtered_lectures:
        clean_message = f"사용자님의 관심사에 맞춘 추천 강의입니다. (총 {len(filtered_lectures)}건)"
    else:
        clean_message = "관심사에 부합하는 강의를 찾지 못했습니다."

    # 반환 계약: AgentResult(message=..., lectures=...) - 변경 금지
    return AgentResult(message=clean_message, lectures=filtered_lectures)
