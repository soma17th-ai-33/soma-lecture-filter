import json
import logging

from app import agents
from app.llm_client import get_client
from app.schemas import AgentRequest, AgentResponse, HistoryMessage

log = logging.getLogger("gateway")
router_log = logging.getLogger("router")

ROUTER_SYSTEM = """\
You are a router. Read the user's input and pick exactly one agent:
- agent1: Provides lecture schedule information, showing only lectures that are currently open for registration.
- agent2: Filters lectures based on date and time.
- agent3: Recommends and filters specific lectures based on the user's personal interests and preferences.
Respond with JSON only: {"agent": "agent1" | "agent2" | "agent3"}.
"""

AGENT_HANDLERS = {
    "agent1": agents.agent1,
    "agent2": agents.agent2,
    "agent3": agents.agent3,
}


async def route(req: AgentRequest) -> str:
    router_log.info("-> LLM call (model=solar-pro3)")
    client = get_client()
    resp = await client.chat.completions.create(
        model="solar-pro3",
        messages=[
            {"role": "system", "content": ROUTER_SYSTEM},
            {"role": "user", "content": req.message},
        ],
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content
    router_log.info("LLM raw response: %s", raw)
    name = json.loads(raw)["agent"]
    if name not in AGENT_HANDLERS:
        router_log.warning("unknown agent '%s', falling back to agent1", name)
        name = "agent1"
    router_log.info("selected: %s", name)
    return name


async def run_gateway(req: AgentRequest) -> AgentResponse:
    log.info(
        "received request | message=%r | history=%d | lectures=%d",
        req.message,
        len(req.history),
        len(req.lectures),
    )
    log.info("-> routing")
    agent_name = await route(req)
    log.info("-> dispatching to %s", agent_name)
    result = await AGENT_HANDLERS[agent_name](req)

    new_history = list(req.history) + [
        HistoryMessage(role="assistant", content=result.message)
    ]
    log.info(
        "response ready | agent=%s | message_len=%d | lectures=%d",
        agent_name,
        len(result.message),
        len(result.lectures),
    )
    return AgentResponse(
        message=result.message,
        history=new_history,
        lectures=result.lectures,
        agent_used=[agent_name],
    )
