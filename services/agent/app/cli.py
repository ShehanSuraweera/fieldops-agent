"""Run one ticket in the terminal and print every step as it happens.

python -m app.cli "Freezer #3 at our Colombo 7 branch stopped cooling" --email ops@freshmart.lk
python -m app.cli "..." --email ops@freshmart.lk --now 2026-10-03T09:00
"""

import argparse
import json
import sys

from app.config import agent_now, parse_now
from app.graph import Agent, build_summary
from app.store import SqlRunStore, StepRecord

KIND_LABEL = {"node": "NODE", "tool": "tool", "llm": "LLM "}


def print_step(step: StepRecord) -> None:
    status = "ok " if step.ok else "ERR"
    name = step.tool_name or ""
    tokens = ""
    if step.kind == "llm":
        tokens = f"  {step.input_tokens or 0}+{step.output_tokens or 0} tok"
    indent = "" if step.kind == "node" else "  "
    label = f"{indent}{KIND_LABEL[step.kind]} {step.node if step.kind == 'node' else name}"
    print(f"{step.seq:>3}  {label:<38} {status} {step.latency_ms:>6} ms{tokens}")
    if step.error:
        print(f"       ! {step.error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the FieldOps agent on one complaint.")
    parser.add_argument("complaint", help="the customer's message")
    parser.add_argument("--email", required=True, help="sender email (identifies the customer)")
    parser.add_argument(
        "--now", help="agent clock, ISO datetime; naive = Colombo time (default: AGENT_NOW or now)"
    )
    args = parser.parse_args(argv)

    now = parse_now(args.now) or agent_now()
    agent = Agent(SqlRunStore())
    run_id = agent.start(args.email, args.complaint, now)
    print(f"Run {run_id}  clock {now.isoformat()}  sender {args.email}\n")
    state = agent.execute(run_id, on_step=print_step)

    run = agent.store.get_run(run_id)
    print(f"\nStatus: {state['status']}   tokens: {run.input_tokens}+{run.output_tokens}")
    if run.error:
        print(f"Reason: {run.error}")
    print(json.dumps(build_summary(state), indent=2, ensure_ascii=False, default=str))
    return 0 if state["status"] in ("scheduled", "needs_info") else 1


if __name__ == "__main__":
    sys.exit(main())
