"""Run the FieldOps eval scenarios against the live agent and write a report.

For each scenario in scenarios/*.yaml:
  1. reseed the mock enterprise systems (POST /admin/reseed) for a fixed date,
  2. start a run (POST /runs) with the agent clock pinned to that date at 09:00,
  3. if the run pauses for approval, answer with the scenario's approval_decision,
  4. compare each expected field with the run summary.

Writes results/<timestamp>.{md,json} and results/latest.{md,json}.

    python run_evals.py                          # all scenarios
    python run_evals.py --only demo-freshmart    # one scenario (id prefix)
    python run_evals.py --group in_stock --delay 10 --min-pass-rate 0.85
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

HERE = Path(__file__).resolve().parent
SEED_DATE = "2026-10-01"  # a Thursday; every scenario's expectations are computed for this seed
RUN_CLOCK = f"{SEED_DATE}T09:00:00+05:30"
APPROVAL_CLOCK = RUN_CLOCK  # decisions are made immediately, so delivery dates match the expectations

# Expected field -> key in the run summary returned by the agent API.
FIELDS: dict[str, str] = {
    "asset_id": "asset_id",
    "fault_code": "fault_code",
    "priority": "priority",
    "po_created": "po_created",
    "approval_required": "approval_required",
    "technician_id": "technician_id",
    "final_status": "status",
    "inspection": "inspection",
    "warranty_claim": "warranty_claim",
    "sla_risk": "sla_risk",
}
GROUPS = {
    "in_stock": 8,
    "po_under_threshold": 5,
    "po_over_threshold": 5,
    "ambiguous_asset": 3,
    "low_confidence": 3,
    "warranty_claim": 3,
    "no_technician_in_sla": 2,
    "unknown_customer": 1,
}


# --- scenarios -------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    id: str
    group: str
    customer_email: str
    text: str
    expect: dict[str, Any]
    approval_decision: str | None
    path: str


def load_scenarios(directory: Path = HERE / "scenarios") -> list[Scenario]:
    scenarios = []
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        unknown = set(data["expect"]) - set(FIELDS)
        if unknown:
            raise ValueError(f"{path.name}: unknown expect fields {sorted(unknown)}")
        if data["group"] not in GROUPS:
            raise ValueError(f"{path.name}: unknown group {data['group']!r}")
        if data.get("approval_decision") not in (None, "approve", "reject"):
            raise ValueError(f"{path.name}: approval_decision must be approve, reject or null")
        scenarios.append(
            Scenario(
                id=data["id"],
                group=data["group"],
                customer_email=data["ticket"]["customer_email"],
                text=data["ticket"]["text"],
                expect=data["expect"],
                approval_decision=data.get("approval_decision"),
                path=path.name,
            )
        )
    ids = [s.id for s in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("scenario ids must be unique")
    return scenarios


def compare(expect: dict[str, Any], summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per expected field: expected value, actual value and whether they match."""
    checks = {}
    for name, expected in expect.items():
        actual = summary.get(FIELDS[name])
        checks[name] = {"expected": expected, "actual": actual, "ok": actual == expected}
    return checks


# --- running ---------------------------------------------------------------------


class EvalClient:
    def __init__(
        self, agent_url: str, agent_key: str, mock_url: str, mock_key: str, timeout_s: float
    ) -> None:
        self.agent = httpx.Client(base_url=agent_url, headers={"X-API-Key": agent_key}, timeout=30)
        self.mock = httpx.Client(base_url=mock_url, headers={"X-API-Key": mock_key}, timeout=60)
        self.timeout_s = timeout_s

    def reseed(self) -> None:
        self.mock.post("/admin/reseed", json={"today": SEED_DATE}).raise_for_status()

    def start(self, scenario: Scenario) -> str:
        body = {"customer_email": scenario.customer_email, "text": scenario.text, "now": RUN_CLOCK}
        response = self.agent.post("/runs", json=body)
        response.raise_for_status()
        return response.json()["run_id"]

    def decide(self, run_id: str, decision: str) -> None:
        body = {"decision": decision, "comment": "eval runner", "now": APPROVAL_CLOCK}
        self.agent.post(f"/runs/{run_id}/approval", json=body).raise_for_status()

    def wait(self, run_id: str) -> dict[str, Any]:
        """Poll until the run leaves 'running' (finished or paused)."""
        deadline = time.monotonic() + self.timeout_s
        while True:
            run = self.agent.get(f"/runs/{run_id}").raise_for_status().json()
            if run["status"] != "running":
                return run
            if time.monotonic() > deadline:
                raise TimeoutError(f"run {run_id} still running after {self.timeout_s:.0f}s")
            time.sleep(0.5)


@dataclass
class Result:
    id: str
    group: str
    passed: bool
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    run_id: str | None = None
    status: str | None = None
    latency_s: float = 0.0
    tokens: int = 0
    tool_calls: int = 0
    tool_errors: list[str] = field(default_factory=list)
    error: str | None = None


def run_scenario(client: EvalClient, scenario: Scenario) -> Result:
    result = Result(id=scenario.id, group=scenario.group, passed=False)
    try:
        client.reseed()
        started = time.monotonic()
        result.run_id = client.start(scenario)
        run = client.wait(result.run_id)
        if run["status"] == "awaiting_approval" and scenario.approval_decision:
            client.decide(result.run_id, scenario.approval_decision)
            run = client.wait(result.run_id)
        result.latency_s = round(time.monotonic() - started, 2)
    except Exception as exc:  # report it as a failed scenario, keep going
        result.error = f"{exc.__class__.__name__}: {exc}"
        return result

    summary = run.get("summary") or {"status": run["status"]}
    result.status = run["status"]
    result.tokens = (run.get("input_tokens") or 0) + (run.get("output_tokens") or 0)
    tools = [s for s in run.get("steps", []) if s["kind"] == "tool"]
    result.tool_calls = len(tools)
    result.tool_errors = [f"{s['tool_name']}: {s['error']}" for s in tools if not s["ok"]]
    result.checks = compare(scenario.expect, summary)
    result.passed = all(c["ok"] for c in result.checks.values())
    if run.get("error") and not result.passed:
        result.error = run["error"]
    return result


# --- reporting -------------------------------------------------------------------


def _rate(passed: int, total: int) -> float:
    return round(passed / total, 4) if total else 0.0


def summarize(results: list[Result], meta: dict[str, Any]) -> dict[str, Any]:
    per_field: dict[str, dict[str, Any]] = {}
    for result in results:
        for name, check in result.checks.items():
            stats = per_field.setdefault(name, {"checked": 0, "passed": 0})
            stats["checked"] += 1
            stats["passed"] += int(check["ok"])
    for stats in per_field.values():
        stats["pass_rate"] = _rate(stats["passed"], stats["checked"])

    per_group: dict[str, dict[str, Any]] = {}
    for result in results:
        stats = per_group.setdefault(result.group, {"scenarios": 0, "passed": 0})
        stats["scenarios"] += 1
        stats["passed"] += int(result.passed)
    for stats in per_group.values():
        stats["pass_rate"] = _rate(stats["passed"], stats["scenarios"])

    completed = [r for r in results if r.error is None or r.status is not None]
    checks_total = sum(s["checked"] for s in per_field.values())
    checks_passed = sum(s["passed"] for s in per_field.values())
    tool_calls = sum(r.tool_calls for r in results)
    tool_errors = sum(len(r.tool_errors) for r in results)
    return {
        **meta,
        "scenarios": len(results),
        "passed": sum(r.passed for r in results),
        "overall_pass_rate": _rate(sum(r.passed for r in results), len(results)),
        "field_accuracy": _rate(checks_passed, checks_total),
        "per_field": dict(sorted(per_field.items())),
        "per_group": per_group,
        "tool_calls": tool_calls,
        "tool_call_errors": tool_errors,
        "avg_latency_s": round(sum(r.latency_s for r in completed) / len(completed), 2) if completed else 0.0,
        "avg_tokens_per_run": round(sum(r.tokens for r in completed) / len(completed)) if completed else 0,
        "results": [r.__dict__ for r in results],
    }


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# FieldOps Agent eval report",
        "",
        f"- Run at: {report['started_at']} ({report['scenarios']} scenarios, seed {report['seed_date']})",
        f"- LLM: {report.get('llm', 'unknown')}",
        f"- **Overall pass rate: {_pct(report['overall_pass_rate'])}** "
        f"({report['passed']}/{report['scenarios']} scenarios with every expected field correct)",
        f"- Field accuracy: {_pct(report['field_accuracy'])}",
        f"- Tool-call errors: {report['tool_call_errors']} of {report['tool_calls']} tool calls",
        f"- Average latency per run: {report['avg_latency_s']} s",
        f"- Average tokens per run: {report['avg_tokens_per_run']}",
        "",
        "## Pass rate per field",
        "",
        "| Field | Passed | Checked | Pass rate |",
        "| --- | --- | --- | --- |",
    ]
    for name, stats in report["per_field"].items():
        lines.append(f"| {name} | {stats['passed']} | {stats['checked']} | {_pct(stats['pass_rate'])} |")
    lines += [
        "",
        "## Pass rate per group",
        "",
        "| Group | Passed | Scenarios | Pass rate |",
        "| --- | --- | --- | --- |",
    ]
    for name, stats in report["per_group"].items():
        lines.append(f"| {name} | {stats['passed']} | {stats['scenarios']} | {_pct(stats['pass_rate'])} |")
    lines += [
        "",
        "## Scenarios",
        "",
        "| Scenario | Result | Status | Latency (s) | Tokens |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in report["results"]:
        mark = "pass" if r["passed"] else "**FAIL**"
        lines.append(f"| {r['id']} | {mark} | {r['status']} | {r['latency_s']} | {r['tokens']} |")
    failures = [r for r in report["results"] if not r["passed"]]
    if failures:
        lines += ["", "## Failures", ""]
        for r in failures:
            lines.append(f"### {r['id']}  (run `{r['run_id']}`)")
            for name, check in r["checks"].items():
                if not check["ok"]:
                    lines.append(f"- `{name}`: expected `{check['expected']}`, got `{check['actual']}`")
            if r["error"]:
                lines.append(f"- error: {r['error']}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_reports(report: dict[str, Any], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = report["started_at"].replace(":", "").replace("-", "")[:15]
    markdown = render_markdown(report)
    body = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    paths = []
    for name in (stamp, "latest"):
        (out_dir / f"{name}.md").write_text(markdown, encoding="utf-8")
        (out_dir / f"{name}.json").write_text(body, encoding="utf-8")
        paths += [out_dir / f"{name}.md", out_dir / f"{name}.json"]
    return paths


# --- main ------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the FieldOps eval scenarios.")
    parser.add_argument("--only", action="append", default=[], help="run scenarios whose id starts with this")
    parser.add_argument("--group", action="append", default=[], help="run only this group (repeatable)")
    parser.add_argument("--delay", type=float, default=0.0, help="seconds to wait between scenarios")
    parser.add_argument("--timeout", type=float, default=300.0, help="seconds to wait for one run")
    parser.add_argument(
        "--min-pass-rate", type=float, default=0.0, help="exit 1 below this overall pass rate"
    )
    parser.add_argument("--results-dir", default=str(HERE / "results"))
    args = parser.parse_args(argv)

    scenarios = load_scenarios()
    if args.only:
        scenarios = [s for s in scenarios if any(s.id.startswith(prefix) for prefix in args.only)]
    if args.group:
        scenarios = [s for s in scenarios if s.group in args.group]
    if not scenarios:
        print("No scenarios selected.")
        return 1

    client = EvalClient(
        os.environ.get("AGENT_URL", "http://localhost:8002"),
        os.environ.get("AGENT_API_KEY", "dev-agent-key"),
        os.environ.get("MOCK_URL", "http://localhost:8001"),
        os.environ.get("MOCK_API_KEY", "dev-mock-key"),
        args.timeout,
    )
    meta = {
        "started_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seed_date": SEED_DATE,
        "run_clock": RUN_CLOCK,
        "llm": f"{os.environ.get('LLM_PROVIDER') or 'gemini'}/{os.environ.get('LLM_MODEL') or 'default'}",
    }
    results = []
    for index, scenario in enumerate(scenarios, 1):
        if index > 1 and args.delay:
            time.sleep(args.delay)
        result = run_scenario(client, scenario)
        results.append(result)
        mark = "PASS" if result.passed else "FAIL"
        misses = [
            f"{n}={c['actual']!r} (want {c['expected']!r})" for n, c in result.checks.items() if not c["ok"]
        ]
        detail = "; ".join(misses) or (result.error or "")
        print(f"[{index:>2}/{len(scenarios)}] {mark}  {scenario.id:<42} {result.latency_s:>6.1f}s  {detail}")

    report = summarize(results, meta)
    paths = write_reports(report, Path(args.results_dir))
    print(
        f"\nOverall pass rate {_pct(report['overall_pass_rate'])} "
        f"({report['passed']}/{report['scenarios']}), "
        f"field accuracy {_pct(report['field_accuracy'])}, tool errors {report['tool_call_errors']}, "
        f"avg latency {report['avg_latency_s']} s, avg tokens {report['avg_tokens_per_run']}"
    )
    print("Report:", ", ".join(str(p) for p in paths))
    return 0 if report["overall_pass_rate"] >= args.min_pass_rate else 1


if __name__ == "__main__":
    sys.exit(main())
