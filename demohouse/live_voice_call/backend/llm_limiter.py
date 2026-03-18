import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator


_DEFAULT_LIMIT = int(os.getenv("LLM_CONCURRENT_REQUESTS", "5"))
_limit = max(1, _DEFAULT_LIMIT)
_semaphore = asyncio.Semaphore(_limit)


def configure_llm_limit(limit: int) -> int:
    global _limit, _semaphore
    _limit = max(1, int(limit))
    _semaphore = asyncio.Semaphore(_limit)
    return _limit


def get_llm_limit() -> int:
    return _limit


@asynccontextmanager
async def llm_slot() -> AsyncIterator[None]:
    await _semaphore.acquire()
    try:
        yield
    finally:
        _semaphore.release()
