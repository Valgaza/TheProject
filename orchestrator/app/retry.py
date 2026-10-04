import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

import httpx

logger = logging.getLogger("orchestrator")

T = TypeVar("T")

# httpx.ConnectError / httpx.TimeoutException = the service was completely
# unreachable, or took too long to respond. These are worth retrying,
# because the problem might just be a slow moment, not a permanent failure.
RETRYABLE_EXCEPTIONS = (httpx.ConnectError, httpx.TimeoutException)


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    name: str,
    max_attempts: int = 3,
    base_delay: float = 0.5,
) -> T:
    """Call an async function, retrying on transient network failures with
    exponential backoff (0.5s, then 1s, then 2s, ...) instead of hammering
    a struggling service immediately again. Gives up after max_attempts and
    re-raises the last error, so the caller's existing failure handling
    (logging + the `warnings` field in main.py) still runs as before.

    Only retries genuinely transient problems:
      - connection failures / timeouts (RETRYABLE_EXCEPTIONS above)
      - HTTP 5xx responses (the *server* had a problem — might recover)
    It deliberately does NOT retry HTTP 4xx responses (e.g. 400 Bad
    Request) — those mean *our* request was wrong, and sending the exact
    same broken request again would just fail the same way every time.
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
