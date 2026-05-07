import logging
import os
from typing import Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

log = logging.getLogger("llm")
_client: Optional[AsyncOpenAI] = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        log.info("initializing AsyncOpenAI client (base_url=https://api.upstage.ai/v1)")
        _client = AsyncOpenAI(
            api_key=os.environ["UPSTAGE_API_KEY"],
            base_url="https://api.upstage.ai/v1",
        )
    return _client
