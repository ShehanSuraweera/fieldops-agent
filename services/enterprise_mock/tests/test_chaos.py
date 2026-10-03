"""CHAOS_MODE middleware: injected errors and latency, exempt paths, off by default."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.chaos import ChaosMiddleware, ChaosSettings
from app.main import CHAOS


def _client(chaos: ChaosSettings, sleeps: list[float] | None = None) -> TestClient:
    app = FastAPI()

    @app.get("/crm/customers")
    def customers() -> list[str]:
        return ["CUST-001"]

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/admin/reseed")
    def reseed() -> dict[str, str]:
        return {"seeded_for": "x"}

    async def fake_sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    app.add_middleware(ChaosMiddleware, chaos=chaos, sleep=fake_sleep)
    return TestClient(app)


def test_off_by_default() -> None:
    assert ChaosSettings().enabled is False
    assert CHAOS.enabled is False  # the test environment does not set CHAOS_MODE


def test_injects_503_with_the_standard_error_body() -> None:
    client = _client(ChaosSettings(enabled=True, error_rate=1.0, max_latency_ms=0))
    response = client.get("/crm/customers")
    assert response.status_code == 503
    assert response.headers["x-chaos"] == "error"
    assert response.json()["error"]["code"] == "chaos_injected"


def test_exempt_paths_are_never_affected() -> None:
    client = _client(ChaosSettings(enabled=True, error_rate=1.0, max_latency_ms=0))
    assert client.get("/health").status_code == 200
    assert client.post("/admin/reseed").status_code == 200


def test_latency_is_bounded_and_requests_still_succeed() -> None:
    sleeps: list[float] = []
    client = _client(ChaosSettings(enabled=True, error_rate=0.0, max_latency_ms=500, seed=1), sleeps)
    for _ in range(20):
        assert client.get("/crm/customers").status_code == 200
    assert len(sleeps) == 20
    assert all(0 <= s <= 0.5 for s in sleeps)


def test_error_rate_is_roughly_respected_and_seeded() -> None:
    def failures(seed: int) -> list[int]:
        client = _client(ChaosSettings(enabled=True, error_rate=0.25, max_latency_ms=0, seed=seed))
        return [client.get("/crm/customers").status_code for _ in range(200)]

    first = failures(7)
    assert 25 <= first.count(503) <= 75
    assert first == failures(7)  # same seed, same sequence


def test_disabled_middleware_passes_through() -> None:
    client = _client(ChaosSettings(enabled=False, error_rate=1.0, max_latency_ms=5000))
    assert client.get("/crm/customers").status_code == 200
