"""CHAOS_MODE: inject random latency and 503 errors into the CRM/FSM/ERP endpoints.

Shows how the agent copes with flaky enterprise systems: idempotent calls (GET, PATCH)
retry and recover, while a failed POST is never blindly retried, so the run goes to
human review instead of risking a duplicate reservation or purchase order.

Errors are injected before the request reaches the handler, so nothing is written.
Health, admin and API docs are never affected.

    CHAOS_MODE=true  CHAOS_ERROR_RATE=0.1  CHAOS_MAX_LATENCY_MS=800  CHAOS_SEED=7
"""

import asyncio
import json
import os
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

EXEMPT_PREFIXES = ("/health", "/admin", "/docs", "/redoc", "/openapi.json")


@dataclass(frozen=True)
class ChaosSettings:
    enabled: bool = False
    error_rate: float = 0.1
    max_latency_ms: int = 800
    seed: int | None = None

    @classmethod
    def from_env(cls) -> "ChaosSettings":
        seed = os.environ.get("CHAOS_SEED")
        return cls(
            enabled=os.environ.get("CHAOS_MODE", "").strip().lower() in ("1", "true", "yes", "on"),
            error_rate=float(os.environ.get("CHAOS_ERROR_RATE") or 0.1),
            max_latency_ms=int(os.environ.get("CHAOS_MAX_LATENCY_MS") or 800),
            seed=int(seed) if seed else None,
        )

    def public(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "error_rate": self.error_rate, "max_latency_ms": self.max_latency_ms}


class ChaosMiddleware:
    """Pure ASGI middleware, so it adds no overhead when disabled."""

    def __init__(
        self,
        app: Callable,
        chaos: ChaosSettings,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.app = app
        self.chaos = chaos
        self.random = random.Random(chaos.seed)
        self.sleep = sleep
        self.injected_errors = 0

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if (
            not self.chaos.enabled
            or scope["type"] != "http"
            or scope["path"].startswith(EXEMPT_PREFIXES)
            or scope["path"] == "/"
        ):
            return await self.app(scope, receive, send)

        if self.chaos.max_latency_ms > 0:
            await self.sleep(self.random.uniform(0, self.chaos.max_latency_ms) / 1000)
        if self.random.random() < self.chaos.error_rate:
            self.injected_errors += 1
            body = json.dumps(
                {
                    "error": {
                        "code": "chaos_injected",
                        "message": "Injected failure (CHAOS_MODE): service temporarily unavailable",
                        "details": None,
                    }
                }
            ).encode()
            headers = [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"x-chaos", b"error"),
            ]
            await send({"type": "http.response.start", "status": 503, "headers": headers})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
