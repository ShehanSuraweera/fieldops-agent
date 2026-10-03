"""Run one ticket in the terminal and print every step as it happens.

python -m app.cli "Freezer #3 at our Colombo 7 branch stopped cooling" --email ops@freshmart.lk
python -m app.cli "..." --email ops@freshmart.lk --now 2026-10-03T09:00
python -m app.cli "..." --email ... --decision approve        # answer an approval pause right away
python -m app.cli --resume run_abc123 --decision reject --comment "Too expensive"
"""

import argparse
import json
import sys

from app.checkpoint import close_checkpointer, postgres_checkpointer
from app.config import agent_now, parse_now
from app.graph import Agent, RunNotPaused, build_summary
from app.state import TicketState
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


def print_outcome(agent: Agent, state: TicketState) -> None:
    run = agent.store.get_run(state["run_id"])
    print(f"\nStatus: {state['status']}   tokens: {run.input_tokens}+{run.output_tokens}")
    if run.error:
        print(f"Reason: {run.error}")
    print(json.dumps(build_summary(state), indent=2, ensure_ascii=False, default=str))
    if state["status"] == "awaiting_approval":
        po = state["purchase_order"]
        print(
            f"\nPaused: {po['id']} from {po['vendor_name']} for LKR {po['total_lkr']:,} "
            "needs manager approval."
            f"\n  {po['reason']}"
            f"\nResume with:\n  python -m app.cli --resume {run.id} --decision approve|reject [--comment ...]"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the FieldOps agent on one complaint.")
    parser.add_argument("complaint", nargs="?", help="the customer's message")
    parser.add_argument("--email", help="sender email (identifies the customer)")
    parser.add_argument(
        "--now", help="agent clock, ISO datetime; naive = Colombo time (default: AGENT_NOW or now)"
    )
    parser.add_argument("--resume", metavar="RUN_ID", help="resume a run that is awaiting approval")
    parser.add_argument("--decision", choices=["approve", "reject"], help="manager decision for a paused PO")
    parser.add_argument("--comment", help="manager comment for the decision")
    args = parser.parse_args(argv)
    if args.resume and not args.decision:
        parser.error("--resume needs --decision")
    if not args.resume and not (args.complaint and args.email):
        parser.error("give a complaint and --email, or --resume RUN_ID --decision ...")

    now = parse_now(args.now)
    checkpointer = postgres_checkpointer(max_size=2)
    agent = Agent(SqlRunStore(), checkpointer=checkpointer)
    try:
        if args.resume:
            print(f"Resuming {args.resume} with decision: {args.decision}\n")
            try:
                state = agent.resume(args.resume, args.decision, args.comment, now, on_step=print_step)
            except RunNotPaused:
                print(f"Run {args.resume} is not awaiting approval.")
                return 1
        else:
            clock = now or agent_now()
            run_id = agent.start(args.email, args.complaint, clock)
            print(f"Run {run_id}  clock {clock.isoformat()}  sender {args.email}\n")
            state = agent.execute(run_id, on_step=print_step)
            if state["status"] == "awaiting_approval" and args.decision:
                print(f"\nPaused for approval; applying --decision {args.decision}\n")
                state = agent.resume(run_id, args.decision, args.comment, now, on_step=print_step)
        print_outcome(agent, state)
    finally:
        close_checkpointer(checkpointer)
    return (
        0
        if state["status"] in ("scheduled", "needs_info", "awaiting_approval", "needs_manual_procurement")
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
