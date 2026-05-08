from fastapi import FastAPI

from app.gateway import run_gateway
from app.logging_setup import configure_logging
from app.schemas import AgentRequest, AgentResponse

configure_logging()

app = FastAPI(title="Soma Lecture Filter Agent")


@app.post("/agent/run", response_model=AgentResponse)
async def agent_run(req: AgentRequest) -> AgentResponse:
    """Tool-calling 라우터로 0~N개 에이전트를 호출하고 결과를 합성해 반환한다."""
    return await run_gateway(req)
