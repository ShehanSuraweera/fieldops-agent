"use client";

import type { Step } from "@/lib/api";
import { humanize } from "@/lib/format";
import { JsonBlock, Spinner } from "@/components/ui";

type NodeGroup = { node: string; key: string; calls: Step[]; done: Step | null };

/** Steps arrive in order: a node's tool and LLM calls, then the node's own row when it finishes. */
function groupByNode(steps: Step[]): NodeGroup[] {
  const groups: NodeGroup[] = [];
  let current: NodeGroup | null = null;
  for (const step of steps) {
    if (!current || current.done || current.node !== step.node) {
      current = { node: step.node, key: `${step.node}-${step.seq}`, calls: [], done: null };
      groups.push(current);
    }
    if (step.kind === "node") current.done = step;
    else current.calls.push(step);
  }
  return groups;
}

function isPause(step: Step | null): boolean {
  return Boolean(step && typeof step.output === "object" && step.output && "paused" in step.output);
}

function CallRow({ step }: { step: Step }) {
  const tokens = step.kind === "llm" ? ` · ${step.input_tokens ?? 0}+${step.output_tokens ?? 0} tok` : "";
  return (
    <details className="group rounded-md border border-slate-200 bg-white">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-1.5 text-sm">
        <span
          className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase ${
            step.kind === "llm" ? "bg-violet-100 text-violet-700" : "bg-slate-100 text-slate-600"
          }`}
        >
          {step.kind}
        </span>
        <span className="font-mono text-xs">{step.tool_name}</span>
        {!step.ok && <span className="rounded bg-rose-100 px-1.5 text-xs text-rose-700">error</span>}
        <span className="ml-auto text-xs text-slate-500">
          {step.latency_ms} ms{tokens}
        </span>
        <span className="text-xs text-slate-400 group-open:rotate-90">▶</span>
      </summary>
      <div className="grid gap-2 border-t border-slate-100 p-3 md:grid-cols-2">
        <div>
          <p className="mb-1 text-xs font-medium text-slate-500">{step.kind === "llm" ? "Prompt" : "Input"}</p>
          <JsonBlock value={step.input} />
        </div>
        <div>
          <p className="mb-1 text-xs font-medium text-slate-500">{step.kind === "llm" ? "Model output" : "Output"}</p>
          <JsonBlock value={step.output} />
        </div>
        {step.error && <p className="text-xs text-rose-700 md:col-span-2">{step.error}</p>}
      </div>
    </details>
  );
}

export function Timeline({ steps, live }: { steps: Step[]; live: boolean }) {
  const groups = groupByNode(steps);
  if (groups.length === 0) {
    return (
      <p className="flex items-center gap-2 text-sm text-slate-500">
        {live && <Spinner />} Waiting for the first step…
      </p>
    );
  }
  return (
    <ol className="space-y-3">
      {groups.map((group, index) => {
        const last = index === groups.length - 1;
        const paused = isPause(group.done);
        const failed = group.done && !group.done.ok;
        const dot = failed ? "bg-rose-500" : paused ? "bg-amber-500" : group.done ? "bg-emerald-500" : "bg-sky-500";
        return (
          <li key={group.key} className="relative pl-6">
            <span className={`absolute left-0 top-1.5 h-3 w-3 rounded-full ${dot}`} />
            {!last && <span className="absolute left-[5px] top-5 h-full w-0.5 bg-slate-200" />}
            <div className="flex items-center gap-2">
              <span className="font-medium">{humanize(group.node)}</span>
              {!group.done && live && <Spinner />}
              {paused && <span className="text-xs text-amber-700">paused for manager approval</span>}
              {group.done && (
                <span className="text-xs text-slate-500">
                  {group.done.latency_ms} ms · step {group.done.seq}
                </span>
              )}
            </div>
            {group.done?.error && <p className="mt-1 text-sm text-rose-700">{group.done.error}</p>}
            {group.calls.length > 0 && (
              <div className="mt-2 space-y-1.5">
                {group.calls.map((step) => (
                  <CallRow key={step.seq} step={step} />
                ))}
              </div>
            )}
            {group.done && !paused && group.done.output != null && (
              <details className="mt-1.5">
                <summary className="cursor-pointer text-xs text-slate-500">state update</summary>
                <div className="mt-1">
                  <JsonBlock value={group.done.output} />
                </div>
              </details>
            )}
          </li>
        );
      })}
    </ol>
  );
}
