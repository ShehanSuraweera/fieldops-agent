"""Agent HTTP API: start runs, follow them live, and approve or reject parked purchase orders."""

import json
import secrets
import time
from collections.abc import Iterator
from datetime import datetime
from functools import lru_cache
from typing import Any, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Query, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.checkpoint import postgres_checkpointer
from app.config import parse_now, settings
from app.graph import Agent, RunNotPaused
from app.metrics import Metrics, compute_metrics
from app.recorder import jsonable
from app.store import RunRecord, SqlRunStore, StepRecord

STREAM_POLL_S = 0.5
STREAM_HEARTBEAT_S = 15.0
STREAM_MAX_S = 600.0

app = FastAPI(
    title="FieldOps Agent API",
    version="0.4.0",
    description=(
        "Starts agent runs, streams their steps, and takes manager decisions on purchase orders "
        "that need approval. Every endpoint except /health needs an `X-API-Key` header. "
        'Errors look like `{"error": {"code", "message", "details"}}`.'
    ),
)


class APIError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code, self.code, self.message = status_code, code, message


def _error(status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": details}}
    return JSONResponse(body, status_code=status_code)


@app.exception_handler(APIError)
async def _api_error(_: Request, exc: APIError) -> JSONResponse:
    return _error(exc.status_code, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", "")} for e in exc.errors()]
    return _error(422, "validation_error", "Request validation failed", details)


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _error(exc.status_code, "not_found" if exc.status_code == 404 else "http_error", str(exc.detail))


_api_key = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: str | None = Security(_api_key)) -> None:
    if key is None or not secrets.compare_digest(key, settings.api_key):
        raise APIError(401, "unauthorized", "Missing or invalid X-API-Key header")


@lru_cache
def get_agent() -> Agent:
    return Agent(SqlRunStore(), checkpointer=postgres_checkpointer())


def _lk(value: datetime | None) -> datetime | None:
    return parse_now(value.isoformat()) if value else None


def _run_or_404(agent: Agent, run_id: str) -> RunRecord:
    run = agent.store.get_run(run_id)
    if run is None:
        raise APIError(404, "not_found", f"run '{run_id}' not found")
    return run


# --- schemas -------------------------------------------------------------------


class RunCreate(BaseModel):
    customer_email: str = Field(min_length=3, max_length=200)
    text: str = Field(min_length=1, max_length=5000)
    now: datetime | None = Field(None, description="Pin the agent's clock for this run (naive = Colombo)")


class RunAccepted(BaseModel):
    run_id: str
    status: str


class RunDetail(RunRecord):
    steps: list[StepRecord]


class ApprovalCreate(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str | None = Field(None, max_length=1000)
    now: datetime | None = Field(None, description="Decision time (default: agent clock); naive = Colombo")


class PendingApproval(BaseModel):
    run_id: str
    ticket_id: str | None
    requested_at: str | None
    priority: str | None
    sla_deadline: str | None
    customer: dict[str, Any]
    asset: dict[str, Any]
    diagnosis: dict[str, Any]
    purchase_order: dict[str, Any] = Field(description="Includes vendor, lines, total, lead time and reason")


# --- routes --------------------------------------------------------------------

protected = [Depends(require_api_key)]


@app.get("/health", tags=["Ops"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/runs", response_model=RunAccepted, status_code=202, tags=["Runs"], dependencies=protected)
def create_run(
    payload: RunCreate, background: BackgroundTasks, agent: Agent = Depends(get_agent)
) -> RunAccepted:
    """Start a run. It executes in the background; follow it on /runs/{run_id}/events."""
    run_id = agent.start(payload.customer_email, payload.text, _lk(payload.now))
    background.add_task(agent.execute, run_id)
    return RunAccepted(run_id=run_id, status="running")


@app.get("/runs", response_model=list[RunRecord], tags=["Runs"], dependencies=protected)
def list_runs(
    limit: int = Query(50, ge=1, le=500),
    status: str | None = Query(None, description="Only runs with this status"),
    agent: Agent = Depends(get_agent),
) -> list[RunRecord]:
    return [run.model_copy(update={"final_state": None}) for run in agent.store.list_runs(limit, status)]


@app.get("/metrics", response_model=Metrics, tags=["Runs"], dependencies=protected)
def metrics(agent: Agent = Depends(get_agent)) -> Metrics:
    """KPIs for the dashboard: auto-resolved rate, time to schedule, approval rate, tokens."""
    return compute_metrics(agent.store.list_runs(limit=100_000))


@app.get("/runs/{run_id}", response_model=RunDetail, tags=["Runs"], dependencies=protected)
def get_run(run_id: str, agent: Agent = Depends(get_agent)) -> RunDetail:
    run = _run_or_404(agent, run_id)
    return RunDetail(**run.model_dump(), steps=agent.store.list_steps(run_id))


def _sse(event: str, data: Any, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(jsonable(data), ensure_ascii=False)}\n\n"


@app.get(
    "/runs/{run_id}/events",
    tags=["Runs"],
    dependencies=protected,
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
def run_events(
    run_id: str,
    request: Request,
    after: int = Query(0, ge=0, description="Only steps after this sequence number"),
    agent: Agent = Depends(get_agent),
) -> StreamingResponse:
    """Server-Sent Events: one `step` event per logged step (id = seq), then a `status` event
    when the run finishes or pauses for approval. Reconnect with Last-Event-ID to continue."""
    _run_or_404(agent, run_id)
    header = request.headers.get("last-event-id", "")
    last_seq = int(header) if header.isdigit() else after

    def stream() -> Iterator[str]:
        nonlocal last_seq
        started = last_beat = time.monotonic()
        yield "retry: 2000\n\n"
        while True:
            # Read the status before the steps: every step of a finished run is written
            # before its status changes, so nothing can be missed.
            run = agent.store.get_run(run_id)
            for step in agent.store.list_steps(run_id, after_seq=last_seq):
                last_seq = step.seq
                yield _sse("step", step.model_dump(), step.seq)
            if run.status != "running":
                yield _sse("status", {"status": run.status, "summary": run.summary, "error": run.error})
                return
            now = time.monotonic()
            if now - started > STREAM_MAX_S:
                yield _sse("status", {"status": run.status, "timeout": True})
                return
            if now - last_beat > STREAM_HEARTBEAT_S:
                last_beat = now
                yield ": keep-alive\n\n"
            time.sleep(STREAM_POLL_S)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)


@app.get(
    "/approvals/pending", response_model=list[PendingApproval], tags=["Approvals"], dependencies=protected
)
def pending_approvals(agent: Agent = Depends(get_agent)) -> list[PendingApproval]:
    """Purchase orders waiting for a manager, with the agent's reasoning."""
    pending = []
    for run in agent.store.list_runs(limit=500, status="awaiting_approval"):
        state = run.final_state or {}
        customer, asset = state.get("customer") or {}, state.get("asset") or {}
        pending.append(
            PendingApproval(
                run_id=run.id,
                ticket_id=run.ticket_id,
                requested_at=(state.get("approval") or {}).get("requested_at"),
                priority=state.get("priority"),
                sla_deadline=state.get("sla_deadline"),
                customer={k: customer.get(k) for k in ("id", "name", "contract_tier")},
                asset={k: asset.get(k) for k in ("id", "name", "model", "site")},
                diagnosis={
                    k: (state.get("diagnosis") or {}).get(k)
                    for k in ("fault_code", "fault_name", "confidence", "reasoning")
                },
                purchase_order=state.get("purchase_order") or {},
            )
        )
    return pending


@app.post(
    "/runs/{run_id}/approval",
    response_model=RunAccepted,
    status_code=202,
    tags=["Approvals"],
    dependencies=protected,
)
def decide_approval(
    run_id: str, payload: ApprovalCreate, background: BackgroundTasks, agent: Agent = Depends(get_agent)
) -> RunAccepted:
    """Approve or reject the run's purchase order; the run resumes in the background."""
    run = _run_or_404(agent, run_id)
    try:
        agent.claim(run_id)
    except RunNotPaused:
        raise APIError(
            409, "not_awaiting_approval", f"run '{run_id}' is '{run.status}', not awaiting approval"
        ) from None
    background.add_task(agent.continue_claimed, run_id, payload.decision, payload.comment, _lk(payload.now))
    return RunAccepted(run_id=run_id, status="running")
