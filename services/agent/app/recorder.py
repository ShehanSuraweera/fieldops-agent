"""Step recorder: one agent.run_steps row per node, tool call and LLM call."""

import time
from collections.abc import Callable
from typing import Any

from pydantic_core import to_jsonable_python

from app.llm import LLMCallRecord
from app.store import RunStore, StepRecord

MAX_TEXT = 4_000  # keep prompts and replies readable in the log without storing megabytes


def jsonable(value: Any) -> Any:
    return to_jsonable_python(value, fallback=str)


class StepRecorder:
    def __init__(
        self, run_id: str, store: RunStore, on_step: Callable[[StepRecord], None] | None = None
    ) -> None:
        self.run_id = run_id
        self.store = store
        self.on_step = on_step
        self.node = "start"
        self.seq = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def record(self, **fields: Any) -> StepRecord:
        self.seq += 1
        step = StepRecord(run_id=self.run_id, seq=self.seq, node=self.node, **fields)
        self.store.add_step(step)
        if self.on_step:
            self.on_step(step)
        return step

    def llm_call(self, call: LLMCallRecord) -> None:
        """Callback for StructuredLLM: every provider call, including failed attempts."""
        self.input_tokens += call.usage.input_tokens
        self.output_tokens += call.usage.output_tokens
        self.record(
            kind="llm",
            tool_name=call.prompt_name,
            input={
                "attempt": call.attempt,
                "system": call.request.system[:MAX_TEXT],
                "user": call.request.user[:MAX_TEXT],
            },
            output=(call.response_text or "")[:MAX_TEXT] or None,
            ok=call.error is None,
            latency_ms=call.latency_ms,
            input_tokens=call.usage.input_tokens,
            output_tokens=call.usage.output_tokens,
            error=call.error,
        )


class LoggedTools:
    """Wraps EnterpriseTools so every call is timed and written to the step log."""

    def __init__(self, tools: Any, recorder: StepRecorder) -> None:
        self._tools = tools
        self._recorder = recorder

    def __getattr__(self, name: str) -> Callable[..., Any]:
        tool = getattr(self._tools, name)
        if not callable(tool) or name.startswith("_"):
            return tool

        def call(**kwargs: Any) -> Any:
            started = time.perf_counter()
            result = tool(**kwargs)
            self._recorder.record(
                kind="tool",
                tool_name=name,
                input=jsonable(kwargs),
                output=result.compact().get("data"),
                ok=result.ok,
                latency_ms=round((time.perf_counter() - started) * 1000),
                error=f"{result.error.code}: {result.error.message}" if result.error else None,
            )
            return result

        return call
