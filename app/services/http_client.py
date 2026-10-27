from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class BudgetExceeded(RuntimeError):
    pass


class ProviderError(RuntimeError):
    def __init__(self, provider: str, code: str, message: str, retryable: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class Budget:
    """Wall-clock budget shared by every stage of one investigation."""

    def __init__(self, seconds: float, max_iterations: int):
        self.deadline = time.monotonic() + seconds
        self.seconds = seconds
        self.max_iterations = max_iterations
        self.iterations = 0

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def exhausted(self) -> bool:
        return self.remaining() <= 0.5

    def timeout_for(self, provider_timeout: float) -> float:
        remaining = self.remaining()
        if remaining <= 0.5:
            raise BudgetExceeded("investigation time budget exhausted")
        return min(provider_timeout, remaining)

    def can_iterate(self) -> bool:
        return self.iterations < self.max_iterations and not self.exhausted()

    def next_iteration(self) -> bool:
        if not self.can_iterate():
            return False
        self.iterations += 1
        return True


@dataclass
class HttpResult:
    status_code: int
    body: Any
    attempts: int
    duration_ms: int


async def request_json(
    client: httpx.AsyncClient,
    provider: str,
    method: str,
    url: str,
    *,
    budget: Budget,
    timeout: float,
    retries: int,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    params: dict[str, Any] | None = None,
) -> HttpResult:
    """Bounded request with exponential backoff and jitter; never retries client errors other than 408/425/429."""
    started = time.perf_counter()
    attempt = 0
    last_error: ProviderError | None = None
    while attempt <= retries:
        attempt += 1
        try:
            per_try = budget.timeout_for(timeout)
        except BudgetExceeded as error:
            raise ProviderError(provider, "budget_exhausted", str(error), retryable=False) from error
        try:
            response = await client.request(
                method, url, headers=headers, json=json_body, params=params, timeout=httpx.Timeout(per_try, connect=min(10.0, per_try))
            )
        except httpx.TimeoutException:
            last_error = ProviderError(provider, "timeout", f"{provider} did not respond within {per_try:.0f}s", retryable=True)
        except httpx.TransportError as error:
            last_error = ProviderError(provider, "unreachable", f"{provider} connection failed ({type(error).__name__})", retryable=True)
        else:
            if response.status_code < 400:
                try:
                    body = response.json()
                except ValueError as error:
                    raise ProviderError(provider, "invalid_response", f"{provider} returned non-JSON content", status_code=response.status_code) from error
                return HttpResult(response.status_code, body, attempt, int((time.perf_counter() - started) * 1000))
            code = "unauthorized" if response.status_code in (401, 403) else f"http_{response.status_code}"
            last_error = ProviderError(
                provider,
                code,
                f"{provider} returned HTTP {response.status_code}",
                retryable=response.status_code in RETRYABLE_STATUS,
                status_code=response.status_code,
            )
        if not last_error.retryable or attempt > retries:
            break
        delay = min(8.0, 0.5 * (2 ** (attempt - 1))) + random.uniform(0, 0.4)
        if delay >= budget.remaining():
            break
        await asyncio.sleep(delay)
    assert last_error is not None
    raise last_error
