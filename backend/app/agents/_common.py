import json
import logging
from typing import List, Tuple

from app.schemas import Lecture

log = logging.getLogger("agents")


def parse_index_response(
    raw: str,
    candidate_lectures: List[Lecture],
    *,
    index_key: str = "indices",
) -> Tuple[str, List[Lecture]]:
    """LLM의 JSON 응답에서 message와 1-based index 리스트를 추출.

    기대 포맷: {"message": "...", "indices": [1, 3, 5]}
    - 1-based: 프롬프트에서 [#1]부터 표기하므로 내부에서 -1 해 0-based로 변환.
    - 범위 밖·비정수·중복은 무시(환각 방지).
    - JSON 파싱 실패 시 (raw, []) 반환.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning("JSON parse failed: %s | raw=%r", e, raw)
        return raw, []

    message = (parsed.get("message") or "").strip()
    indices = parsed.get(index_key) or []
    if not isinstance(indices, list):
        indices = []

    n = len(candidate_lectures)
    seen: set[int] = set()
    filtered: List[Lecture] = []
    for raw_idx in indices:
        try:
            i = int(raw_idx) - 1
        except (TypeError, ValueError):
            continue
        if i < 0 or i >= n or i in seen:
            continue
        seen.add(i)
        filtered.append(candidate_lectures[i])

    log.info(
        "parsed indices=%d | matched=%d (of %d candidates)",
        len(indices),
        len(filtered),
        n,
    )
    return message, filtered
