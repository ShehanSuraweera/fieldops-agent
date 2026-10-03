import type { ReactNode } from "react";
import type { RunStatus } from "@/lib/api";
import { humanize } from "@/lib/format";

const STATUS_STYLE: Record<RunStatus, string> = {
  running: "bg-sky-100 text-sky-800",
  awaiting_approval: "bg-amber-100 text-amber-800",
  scheduled: "bg-emerald-100 text-emerald-800",
  needs_info: "bg-violet-100 text-violet-800",
  needs_manual_procurement: "bg-orange-100 text-orange-800",
  needs_human: "bg-rose-100 text-rose-800",
};

export function StatusBadge({ status }: { status: RunStatus | string }) {
  const style = STATUS_STYLE[status as RunStatus] ?? "bg-slate-100 text-slate-700";
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${style}`}>
      {status === "running" && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-500" />}
      {humanize(status)}
    </span>
  );
}

export function Card({ title, children, actions }: { title?: ReactNode; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      {(title || actions) && (
        <div className="mb-3 flex items-center justify-between gap-3">
          {title && <h2 className="text-sm font-semibold text-slate-700">{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-sm text-slate-900">{children}</dd>
    </div>
  );
}

export function JsonBlock({ value }: { value: unknown }) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <pre className="max-h-80 overflow-auto rounded-md bg-slate-900 p-3 font-mono text-xs leading-relaxed text-slate-100">
      {text ?? "null"}
    </pre>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return <p className="rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">{message}</p>;
}

export function Spinner() {
  return <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-sky-500 border-t-transparent" />;
}
