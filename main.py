from fastapi import FastAPI

from app.gateway import run_gateway
from app.logging_setup import configure_logging
from app.schemas import AgentRequest, AgentResponse

configure_logging()

app = FastAPI(title="Soma Lecture Filter Agent")


@app.post("/agent/run", response_model=AgentResponse)
async def agent_run(req: AgentRequest) -> AgentResponse:
    return await run_gateway(req)
