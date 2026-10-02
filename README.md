# FieldOps Agent

An autonomous AI agent that runs CoolTech Services' field service process (from a customer's freezer
complaint to a booked technician) across mock CRM, ERP and FSM systems. It is built in seven phases,
from mock enterprise systems up to an MCP server and dashboard.

**Status:** Phase 1 (foundation and mock enterprise systems) is complete.

## What exists so far

| Piece | Where | Notes |
| --- | --- | --- |
| Postgres 16 | `docker-compose.yml` | One database with schemas `crm`, `fsm`, `erp`, `agent` |
| Enterprise mock API | `services/enterprise_mock/` | FastAPI app with CRM, FSM and ERP routers |
| Migrations | `services/enterprise_mock/alembic/` | Run automatically when the container starts |
| Seed data | `services/enterprise_mock/app/seed.py` | Deterministic: same reference date → same data |
| Tests | `services/enterprise_mock/tests/` | Run against a real Postgres test database (`fieldops_test`) |

## How to run

Prerequisite: Docker with Compose v2. You don't need Python on the host.

```bash
cp .env.example .env            # optional; every value has a default
docker compose up -d --build --wait
```

This starts Postgres and the enterprise mock on <http://localhost:8001>. On first start, the container
migrates the database and seeds it. Later restarts keep existing data.

- API docs: <http://localhost:8001/docs>
- Every endpoint except `/health` needs the header `X-API-Key: dev-mock-key` (set by `MOCK_API_KEY`).
- Errors always have the shape `{"error": {"code", "message", "details"}}`.

### Check the FreshMart demo case

```bash
H="X-API-Key: dev-mock-key"
curl -H "$H" "http://localhost:8001/crm/customers?email=ops@freshmart.lk"
curl -H "$H" "http://localhost:8001/fsm/assets?customer_id=CUST-001&q=Freezer%20%233"
curl -H "$H" "http://localhost:8001/fsm/assets/FRZ-1043/history"
```

Expected results: FreshMart is `CUST-001` (gold tier, Colombo). Freezer #3 is `FRZ-1043`, which is under
warranty. Its newest history record is `COMP_FAIL`, 60 days before the seed date, done by `TECH-02`.

### Run the tests and linter

```bash
docker compose build enterprise_mock
docker compose run --rm enterprise_mock pytest
docker compose run --rm enterprise_mock sh -c "ruff check . && ruff format --check ."
```

The tests create and migrate a separate `fieldops_test` database, so they never touch your dev data.

### Reseed or reset

```bash
docker compose exec enterprise_mock python -m app.seed --reset                    # reseed for today
docker compose exec enterprise_mock python -m app.seed --reset --today 2026-10-01 # pin the date
docker compose down -v                                                            # wipe everything
```

All seeded dates are offsets from a reference date: `SEED_TODAY` in `.env`, `--today`, or today in
Colombo. Pin the date when you need fully reproducible data.

## Seed data at a glance

- 20 customers across Colombo, Kandy, Galle and Kurunegala (gold, silver and bronze contracts)
- 50 assets across 5 models (`AP-500`, `AP-900`, `FL-140` freezers; `PM-220`, `CC-75` chillers). Two
  customers have two assets with the same name at different sites, for testing clarifying questions.
- 120 service records, including 6 assets with repeat failures within 90 days
- 12 fault codes, 30 parts (several at zero stock), 6 vendors (4 approved)
- 8 technicians with 14 days of 2-hour slots (08:00–18:00, no Sundays). `TECH-08`, Kurunegala's only
  technician, is fully booked for the first 5 days.
- Money is stored in whole LKR. Times are Asia/Colombo (UTC+05:30).
