"""Agent HTTP API: start runs and read their status, summary and steps."""

import secrets
from datetime import datetime
from functools import lru_cache
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Query, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import parse_now, settings
from app.graph import Agent
from app.store import RunRecord, SqlRunStore, StepRecord

app = FastAPI(
    title="FieldOps Agent API",
    version="0.3.0",
    description=(
        "Starts agent runs and reports their progress. Every endpoint except /health needs an "
        '`X-API-Key` header. Errors look like `{"error": {"code", "message", "details"}}`.'
    ),
)


class APIError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code, self.code, self.message = status_code, code, message


def _error(status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message, "details": details}}, status_code=status_code
    )


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
    return Agent(SqlRunStore())


# --- schemas -------------------------------------------------------------------


class RunCreate(BaseModel):
    customer_email: str = Field(min_length=3, max_length=200)
    text: str = Field(min_length=1, max_length=5000)
    now: datetime | None = Field(None, description="Pin the agent's clock for this run (naive = Colombo)")


class RunCreated(BaseModel):
    run_id: str
    status: str


class RunDetail(RunRecord):
    steps: list[StepRecord]


# --- routes --------------------------------------------------------------------


@app.get("/health", tags=["Ops"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/runs",
    response_model=RunCreated,
    status_code=202,
    tags=["Runs"],
    dependencies=[Depends(require_api_key)],
)
def create_run(
    payload: RunCreate, background: BackgroundTasks, agent: Agent = Depends(get_agent)
) -> RunCreated:
    """Start a run. It executes in the background; poll GET /runs/{run_id} for the outcome."""
    now = parse_now(payload.now.isoformat()) if payload.now else None
    run_id = agent.start(payload.customer_email, payload.text, now)
    background.add_task(agent.execute, run_id)
    return RunCreated(run_id=run_id, status="running")


@app.get("/runs", response_model=list[RunRecord], tags=["Runs"], dependencies=[Depends(require_api_key)])
def list_runs(limit: int = Query(50, ge=1, le=500), agent: Agent = Depends(get_agent)) -> list[RunRecord]:
    return [run.model_copy(update={"final_state": None}) for run in agent.store.list_runs(limit)]


@app.get("/runs/{run_id}", response_model=RunDetail, tags=["Runs"], dependencies=[Depends(require_api_key)])
def get_run(run_id: str, agent: Agent = Depends(get_agent)) -> RunDetail:
    run = agent.store.get_run(run_id)
    if run is None:
        raise APIError(404, "not_found", f"run '{run_id}' not found")
    return RunDetail(**run.model_dump(), steps=agent.store.list_steps(run_id))
