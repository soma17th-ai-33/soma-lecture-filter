from typing import List, Optional

from pydantic import BaseModel


class HistoryMessage(BaseModel):
    role: str
    content: str


class Lecture(BaseModel):
    author: str
    dateStr: str
    timeRangeStr: str
    title: str
    url: str
    is_open: Optional[bool] = None


class AgentRequest(BaseModel):
    message: str
    history: List[HistoryMessage]
    lectures: List[Lecture]


class AgentResult(BaseModel):
    message: str
    lectures: List[Lecture]


class AgentResponse(BaseModel):
    message: str
    history: List[HistoryMessage]
    lectures: List[Lecture]
    agent_used: List[str]
