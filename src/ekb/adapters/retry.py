import asyncio
import random
from collections.abc import Awaitable, Callable

import httpx

from ekb.observability import log, tracer

DEFAULT_RETRY_ATTEMPTS = 3


def transient(error: Exception) -> bool:
    return isinstance(error, httpx.TransportError) or (
        isinstance(error, httpx.HTTPStatusError)
        and (error.response.status_code in {408, 429} or error.response.status_code >= 500)
    )


async def retry[T](
    operation: Callable[[], Awaitable[T]],
    attempts: int = DEFAULT_RETRY_ATTEMPTS,
    base_delay: float = 0.5,
) -> T:
    for attempt in range(attempts):
        try:
            return await operation()
        except Exception as error:
            if attempt + 1 == attempts or not transient(error):
                raise
            with tracer.start_as_current_span(
                "retry",
                attributes={"retry.attempt": attempt + 1, "error.type": type(error).__name__},
            ):
                log.warning("transient_retry", attempt=attempt + 1, error_type=type(error).__name__)
                await asyncio.sleep(base_delay * 2**attempt + random.uniform(0, base_delay))
    raise AssertionError("Retry requires at least one attempt")
