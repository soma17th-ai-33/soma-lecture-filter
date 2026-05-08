import asyncio
import logging
import os
from typing import Optional

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

load_dotenv()

log = logging.getLogger("llm")
_client: Optional[AsyncOpenAI] = None


LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "20"))
LLM_MAX_ATTEMPTS = int(os.getenv("LLM_MAX_ATTEMPTS", "3"))
LLM_BACKOFF_BASE_S = 0.5

RETRYABLE_EXC = (
    asyncio.TimeoutError,
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    InternalServerError,
)


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        log.info("initializing AsyncOpenAI client (base_url=https://api.upstage.ai/v1)")
        _client = AsyncOpenAI(
            api_key=os.environ["UPSTAGE_API_KEY"],
            base_url="https://api.upstage.ai/v1",
        )
    return _client


async def llm_call(
    *,
    timeout_s: float = LLM_TIMEOUT_S,
    max_attempts: int = LLM_MAX_ATTEMPTS,
    **chat_kwargs,
):
    """chat.completions.create + asyncio.wait_for + 지수 백오프 재시도.

    재시도: RETRYABLE_EXC만. AuthenticationError·BadRequestError 등은 즉시 전파.
    """
    client = get_client()
    last_exc: Optional[BaseException] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await asyncio.wait_for(
                client.chat.completions.create(**chat_kwargs),
                timeout=timeout_s,
            )
        except RETRYABLE_EXC as e:
            last_exc = e
            if attempt == max_attempts:
                break
            sleep_s = LLM_BACKOFF_BASE_S * (2 ** (attempt - 1))
            log.warning(
                "llm_call retry %d/%d after %s: %s",
                attempt,
                max_attempts,
                type(e).__name__,
                e,
            )
            await asyncio.sleep(sleep_s)
    assert last_exc is not None
    raise last_exc
