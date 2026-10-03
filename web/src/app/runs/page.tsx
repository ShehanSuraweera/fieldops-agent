"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api, type Metrics } from "@/lib/api";
import { formatDateTime, formatDuration, formatPercent, secondsBetween } from "@/lib/format";
import { Card, ErrorNote, StatusBadge } from "@/components/ui";

function Kpi({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold">{value}</p>
      <p className="mt-1 text-xs text-slate-500">{hint}</p>
    </div>
  );
}

function KpiCards({ metrics }: { metrics: Metrics | undefined }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Kpi
        label="Auto-resolved"
        value={formatPercent(metrics?.auto_resolved_rate)}
        hint={`Scheduled with no human step, of ${metrics?.finished_runs ?? 0} finished runs`}
      />
      <Kpi
        label="Avg time to schedule"
        value={formatDuration(metrics?.avg_time_to_schedule_s)}
        hint="From complaint to booked visit, including approval waits"
      />
      <Kpi
        label="Approval rate"
        value={formatPercent(metrics?.approval_rate)}
        hint={`POs approved, of ${metrics?.approvals_decided ?? 0} manager decisions`}
      />
      <Kpi
        label="Avg tokens per run"
        value={metrics?.avg_tokens_per_run == null ? "—" : Math.round(metrics.avg_tokens_per_run).toLocaleString()}
        hint="LLM input + output tokens, finished runs"
      />
    </div>
  );
}

export default function RunsPage() {
  const router = useRouter();
  const metrics = useQuery({ queryKey: ["metrics"], queryFn: api.metrics, refetchInterval: 5_000 });
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api.runs(100), refetchInterval: 5_000 });

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Runs</h1>
          <p className="mt-1 text-sm text-slate-600">
            Every agent run and its outcome. {metrics.data?.running ?? 0} running,{" "}
            {metrics.data?.awaiting_approval ?? 0} waiting for approval.
          </p>
        </div>
        <Link href="/" className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-700">
          New ticket
        </Link>
      </div>

      <KpiCards metrics={metrics.data} />
      <ErrorNote error={metrics.error ?? runs.error} />

      <Card title={`Recent runs (${runs.data?.length ?? 0})`}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="px-2 py-2">Started</th>
                <th className="px-2 py-2">Customer</th>
                <th className="px-2 py-2">Asset</th>
                <th className="px-2 py-2">Status</th>
                <th className="px-2 py-2">Priority</th>
                <th className="px-2 py-2">Technician</th>
                <th className="px-2 py-2 text-right">Tokens</th>
                <th className="px-2 py-2 text-right">Duration</th>
              </tr>
            </thead>
            <tbody>
              {runs.data?.map((run) => (
                <tr
                  key={run.id}
                  onClick={() => router.push(`/runs/${run.id}`)}
                  className="cursor-pointer border-t border-slate-100 hover:bg-slate-50"
                >
                  <td className="whitespace-nowrap px-2 py-2">{formatDateTime(run.created_at)}</td>
                  <td className="px-2 py-2">{run.customer_email}</td>
                  <td className="px-2 py-2 font-mono text-xs">{run.summary?.asset_id ?? "—"}</td>
                  <td className="px-2 py-2">
                    <StatusBadge status={run.status} />
                  </td>
                  <td className="px-2 py-2">{run.summary?.priority ?? "—"}</td>
                  <td className="px-2 py-2">{run.summary?.technician_id ?? "—"}</td>
                  <td className="px-2 py-2 text-right">{(run.input_tokens + run.output_tokens).toLocaleString()}</td>
                  <td className="px-2 py-2 text-right">
                    {formatDuration(secondsBetween(run.created_at, run.finished_at))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {runs.data?.length === 0 && <p className="p-2 text-sm text-slate-500">No runs yet.</p>}
        </div>
      </Card>
    </div>
  );
}
