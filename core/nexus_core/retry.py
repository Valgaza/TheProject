"""Retrying a transient failure instead of giving up after one try.

Ported over from the orchestrator/ scaffold built on feature/rag-pipeline,
which built this before core/ existed. Written generically (no RAG-specific
assumptions) so any future client here — vision, speech — can reuse it rather
than each writing its own copy.

Not every failure is worth retrying: a request that was wrong the first time
(HTTP 4xx) will be wrong every time, so retrying it just adds latency without
a chance of success. Only genuinely transient problems — connection failures,
timeouts, and 5xx responses (the *server* had a problem, might recover) — are
retried, with exponential backoff so a struggling service isn't hammered.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

RETRYABLE_EXCEPTIONS = (httpx.ConnectError, httpx.TimeoutException)


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    name: str,
    max_attempts: int = 3,
    base_delay: float = 0.5,
) -> T:
    """Call an async function, retrying transient failures with exponential
    backoff (0.5s, 1s, 2s, ...). Gives up after max_attempts and re-raises the
    last error — this function doesn't decide what "give up" means, the
    caller does (memory_client-style: catch it and degrade the answer).
    """
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except RETRYABLE_EXCEPTIONS as exc:
            last_exc = exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise  # our request was bad — retrying won't help, fail now
            last_exc = exc

        if attempt < max_attempts:
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "%s: attempt %d/%d failed (%s), retrying in %.1fs",
                name, attempt, max_attempts, last_exc, delay,
            )
            await asyncio.sleep(delay)

    logger.warning("%s: all %d attempts failed", name, max_attempts)
    assert last_exc is not None
    raise last_exc
