# FieldOps Agent

An autonomous AI agent that runs CoolTech Services' field service process (from a customer's freezer
complaint to a booked technician) across mock CRM, ERP and FSM systems. It is built in seven phases,
from mock enterprise systems up to an MCP server and dashboard.

**Status:** Phases 1–4 are complete: mock enterprise systems, business rules and tools, the agent graph
with its API and CLI, and manager approval of large purchase orders (runs pause, survive restarts and
resume).

## What exists so far

| Piece | Where | Notes |
| --- | --- | --- |
| Postgres 16 | `docker-compose.yml` | One database with schemas `crm`, `fsm`, `erp`, `agent` |
| Enterprise mock API | `services/enterprise_mock/` | FastAPI app with CRM, FSM and ERP routers |
| Migrations | `services/*/alembic/` | Run automatically when each container starts |
| Seed data | `services/enterprise_mock/app/seed.py` | Deterministic: same reference date → same data |
| Business rules | `services/agent/app/rules.py` | Pure functions; every threshold read from `config/rules.yaml` |
| Enterprise tools | `services/agent/app/tools.py` | Typed httpx wrappers returning `{ok, data, error}`, with timeouts and retries |
| Agent graph | `services/agent/app/graph.py`, `nodes/` | LangGraph: intake → identify → assess → diagnose → prioritize → parts → procure → schedule → finalize |
| LLM wrapper | `services/agent/app/llm.py`, `prompts/` | Gemini (default), OpenAI or Groq; every output validated by Pydantic, retried once |
| Run log | `agent.runs`, `agent.run_steps` | One row per node, tool call and LLM call (input, output, latency, tokens, error) |
| Agent API and CLI | `services/agent/app/api.py`, `cli.py` | API docs at <http://localhost:8002/docs>; the CLI prints the step trace |
| Human approval | `nodes/parts.py` (`approve_po`), `checkpoint.py` | POs over LKR 100,000 pause the run with LangGraph `interrupt()`; state is checkpointed in Postgres |
| Live events | `GET /runs/{id}/events` | Server-Sent Events: one event per step, then the final or paused status |

The LLM reads the complaint, picks a fault code from the catalog and words the customer messages. Plain
Python decides everything else: priority, SLA, warranty, vendor, PO approval, technician, dates and money.

## How to run

Prerequisite: Docker with Compose v2. You don't need Python on the host.

```bash
cp .env.example .env            # then set GEMINI_API_KEY (or another provider's key) in .env
docker compose up -d --build --wait
```

This starts Postgres, the enterprise mock on <http://localhost:8001> and the agent API on
<http://localhost:8002>. Only the agent's runs need an LLM key; the rest works without one. On first
start, the mock migrates the database and seeds it. Later restarts keep existing data.

- Mock API docs: <http://localhost:8001/docs> (header `X-API-Key: dev-mock-key`, set by `MOCK_API_KEY`)
- Agent API docs: <http://localhost:8002/docs> (header `X-API-Key: dev-agent-key`, set by `AGENT_API_KEY`)
- Errors always have the shape `{"error": {"code", "message", "details"}}`.

### Run the FreshMart demo

The demo is deterministic. On the seed date, TECH-02 is always free from 10:00 to 16:00, so pin the
agent's clock to 09:00 that day. Pick a date that is not a Sunday, because there are no Sunday shifts.

```bash
D=2026-10-03                                             # use today's date
docker compose exec enterprise_mock python -m app.seed --reset --today $D
docker compose exec agent python -m app.cli "Freezer #3 at our Colombo 7 branch stopped cooling again this morning." --email ops@freshmart.lk --now ${D}T09:00
```

The CLI prints each node, tool call and LLM call as it happens, followed by a summary. Expect
`"status": "scheduled"`, `"asset_id": "FRZ-1043"`, `"priority": "P1"`, `"technician_id": "TECH-02"` and a
10:00 start. The compressor `CMP-AP` is reserved from stock, so no PO is created. The demo books TECH-02's
slot, so reseed before running it again.

The same run through the API (runs execute in the background; poll for the result):

```bash
H="X-API-Key: dev-agent-key"
curl -X POST localhost:8002/runs -H "$H" -H "Content-Type: application/json" -d '{"customer_email": "ops@freshmart.lk", "text": "Freezer #3 at our Colombo 7 branch stopped cooling.", "now": "2026-10-03T09:00:00"}'
curl -H "$H" localhost:8002/runs/<run_id>      # status, summary and every logged step
curl -H "$H" localhost:8002/runs               # recent runs
```

### Approve or reject a purchase order

A PO above LKR 100,000 (`po_approval_threshold_lkr` in `config/rules.yaml`) pauses the run as
`awaiting_approval`. The PO is `pending_approval` in the ERP, and nothing is booked yet. The paused run
lives in Postgres, so it survives `docker compose restart agent`.

```bash
D=2026-10-03
docker compose exec enterprise_mock python -m app.seed --reset --today $D
docker compose exec agent python -m app.cli "Freezer #2 at Katugastota stopped cooling. The compressor will not start, it just clicks every few seconds." --email ops@kandycold.lk --now ${D}T09:00
# -> Status: awaiting_approval, plus the resume command, e.g.:
docker compose exec agent python -m app.cli --resume <run_id> --decision approve --comment "OK"
```

Add `--decision approve|reject` to the first command to answer the pause straight away. The same
through the API:

```bash
H="X-API-Key: dev-agent-key"
curl -H "$H" localhost:8002/approvals/pending        # PO, vendor reasoning, diagnosis, ticket
curl -X POST localhost:8002/runs/<run_id>/approval -H "$H" -H "Content-Type: application/json" -d '{"decision": "reject", "comment": "Use refurbished stock"}'
curl -N -H "$H" localhost:8002/runs/<run_id>/events  # live step stream (Server-Sent Events)
```

- **Approve:** the PO goes to `sent`. The vendor's lead time starts at approval, and the visit is booked
  after the parts arrive. The run ends `scheduled`.
- **Reject:** the PO goes to `rejected` and the ticket to `needs_manual_procurement`. The agent books an
  inspection-only visit (not before the decision time) and tells the customer about the parts delay.
  The run ends `needs_manual_procurement`.
- A second decision on the same run gets `409 not_awaiting_approval`.

To switch provider, set `LLM_PROVIDER=openai` or `groq` (and optionally `LLM_MODEL`) plus that
provider's key in `.env`. Then run `docker compose up -d` to restart the agent with the new settings.
Without a key, a run ends as `needs_human` with the reason recorded.

### Check the mock data directly

```bash
H="X-API-Key: dev-mock-key"
curl -H "$H" "http://localhost:8001/crm/customers?email=ops@freshmart.lk"
curl -H "$H" "http://localhost:8001/fsm/assets?customer_id=CUST-001&q=Freezer%20%233"
curl -H "$H" "http://localhost:8001/fsm/assets/FRZ-1043/history"
```

FreshMart is `CUST-001` (gold tier, Colombo). Freezer #3 is `FRZ-1043`, which is under warranty. Its
newest history record is `COMP_FAIL`, 60 days before the seed date, done by `TECH-02`.

### Run the tests and linters

```bash
docker compose build
docker compose run --rm enterprise_mock pytest
docker compose run --rm agent pytest
docker compose run --rm enterprise_mock sh -c "ruff check . && ruff format --check ."
docker compose run --rm agent sh -c "ruff check . && ruff format --check ."
```

None of the tests need an LLM key or touch your dev data:

- The mock tests use their own `fieldops_test` database.
- The agent's graph tests run against an in-memory enterprise and a scripted LLM.
- The LLM wrapper is tested against fake provider endpoints.
- The approval tests pause and resume runs with an in-memory checkpointer.
- The run-store, restart and API tests use a `fieldops_agent_test` database. The restart test pauses a
  run with one Postgres checkpointer and resumes it with a brand-new one.
- `test_tools_live.py` makes read-only calls against the running mock.

To change a business-rule threshold, edit `config/rules.yaml`. It is mounted into the agent container,
so you don't need to rebuild.

### Reseed or reset

```bash
docker compose exec enterprise_mock python -m app.seed --reset                    # reseed for today
docker compose exec enterprise_mock python -m app.seed --reset --today 2026-10-01 # pin the date
docker compose down -v                                                            # wipe everything
```

All seeded dates are offsets from a reference date: `SEED_TODAY` in `.env`, `--today`, or today in
Colombo. Pin the date when you need fully reproducible data. The agent's clock is separate: `--now` on
the CLI, `now` in `POST /runs`, or `AGENT_NOW` in `.env`.

## Seed data at a glance

- 20 customers across Colombo, Kandy, Galle and Kurunegala (gold, silver and bronze contracts)
- 50 assets across 5 models (`AP-500`, `AP-900`, `FL-140` freezers; `PM-220`, `CC-75` chillers). Two
  customers have two assets with the same name at different sites, for testing clarifying questions.
- 120 service records, including 6 assets with repeat failures within 90 days
- 12 fault codes, 30 parts (several at zero stock), 6 vendors (4 approved)
- 8 technicians with 14 days of 2-hour slots (08:00–18:00, no Sundays). `TECH-08`, Kurunegala's only
  technician, is fully booked for the first 5 days. `TECH-02` is always free 10:00–16:00 on the seed date.
- Money is stored in whole LKR. Times are Asia/Colombo (UTC+05:30).
