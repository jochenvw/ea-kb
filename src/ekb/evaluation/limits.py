from __future__ import annotations

import asyncio
from asyncio import sleep
from collections import deque
from time import monotonic


class DeploymentRateLimiter:
    """Reserve each transport request and its estimated tokens in a rolling minute."""

    def __init__(
        self,
        *,
        requests_per_minute: int | None = None,
        tokens_per_minute: int | None = None,
        estimated_tokens_per_request: int | None = None,
    ) -> None:
        if requests_per_minute is None and tokens_per_minute is None:
            raise ValueError("Configure at least one evaluation rate limit")
        if any(
            value is not None and value < 1
            for value in (requests_per_minute, tokens_per_minute, estimated_tokens_per_request)
        ):
            raise ValueError("Evaluation rate limits must be positive")
        if tokens_per_minute is not None and (
            estimated_tokens_per_request is None or estimated_tokens_per_request < 1
        ):
            raise ValueError("A positive per-request token estimate is required")
        self.requests_per_minute = requests_per_minute
        self.tokens_per_minute = tokens_per_minute
        self.estimated_tokens_per_request = estimated_tokens_per_request
        self._events: dict[str, deque[tuple[float, int, int]]] = {}
        self._total_requests: dict[str, int] = {}
        self._lock = asyncio.Lock()

    def check_capacity(self, requests: int) -> None:
        if requests < 1:
            raise ValueError("A request reservation must be positive")
        tokens = requests * (self.estimated_tokens_per_request or 0)
        if (self.requests_per_minute is not None and requests > self.requests_per_minute) or (
            self.tokens_per_minute is not None and tokens > self.tokens_per_minute
        ):
            raise ValueError("A request reservation exceeds the configured per-minute limit")

    async def reserve(self, deployment: str, requests: int) -> None:
        self.check_capacity(requests)
        tokens = requests * (self.estimated_tokens_per_request or 0)
        while True:
            async with self._lock:
                now = monotonic()
                events = self._events.setdefault(deployment, deque())
                while events and events[0][0] <= now - 60:
                    events.popleft()
                used_requests = sum(event[1] for event in events)
                used_tokens = sum(event[2] for event in events)
                if (
                    self.requests_per_minute is None
                    or used_requests + requests <= self.requests_per_minute
                ) and (
                    self.tokens_per_minute is None or used_tokens + tokens <= self.tokens_per_minute
                ):
                    events.append((now, requests, tokens))
                    self._total_requests[deployment] = (
                        self._total_requests.get(deployment, 0) + requests
                    )
                    return
                delay = max(events[0][0] + 60 - now, 0.001)
            await sleep(delay)

    def requests_reserved(self) -> dict[str, int]:
        return self._total_requests.copy()

    async def wait_until_clear(self) -> None:
        while True:
            async with self._lock:
                latest = max(
                    (events[-1][0] for events in self._events.values() if events),
                    default=None,
                )
                if latest is None:
                    return
                delay = latest + 60 - monotonic()
                if delay <= 0:
                    return
            await sleep(delay)

    def configuration(self) -> dict[str, int | None]:
        return {
            "requests_per_minute": self.requests_per_minute,
            "tokens_per_minute": self.tokens_per_minute,
            "estimated_tokens_per_request": self.estimated_tokens_per_request,
            "window_seconds": 60,
        }
