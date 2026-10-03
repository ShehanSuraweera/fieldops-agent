import type { Run } from "@/lib/api";
import { formatDateTime, formatLkr, formatPercent, humanize } from "@/lib/format";
import { Card, Field, StatusBadge } from "@/components/ui";

export function SummaryCard({ run }: { run: Run }) {
  const summary = run.summary;
  const state = run.final_state ?? {};
  if (!summary) return null;
  const diagnosis = state.diagnosis;
  const po = state.purchase_order;
  const approval = state.approval;
  const workOrder = state.work_order;

  return (
    <Card title="Outcome" actions={<StatusBadge status={run.status} />}>
      <dl className="grid gap-4 sm:grid-cols-2">
        <Field label="Priority / SLA deadline">
          {summary.priority ?? "—"} · {formatDateTime(summary.sla_deadline)}
        </Field>
        <Field label="Asset">
          {state.asset ? `${state.asset.name} at ${state.asset.site} (${state.asset.id}, ${state.asset.model})` : "—"}
          {state.asset_facts?.under_warranty && (
            <span className="ml-2 rounded bg-emerald-50 px-1.5 text-xs text-emerald-700">under warranty</span>
          )}
          {state.asset_facts?.repeat_failure_90d && (
            <span className="ml-1 rounded bg-amber-50 px-1.5 text-xs text-amber-700">repeat failure</span>
          )}
        </Field>
        {diagnosis && (
          <div className="sm:col-span-2">
            <Field label="Diagnosis">
              <span className="font-medium">{diagnosis.fault_name}</span>{" "}
              <span className="font-mono text-xs">({diagnosis.fault_code})</span> · confidence{" "}
              {formatPercent(diagnosis.confidence)}
              {diagnosis.inspection && (
                <span className="ml-2 rounded bg-violet-50 px-1.5 text-xs text-violet-700">inspection visit</span>
              )}
              <p className="mt-1 text-slate-600">{diagnosis.reasoning}</p>
            </Field>
          </div>
        )}
        <Field label="Parts">
          {summary.parts.length === 0
            ? "None"
            : summary.parts.map((part) => (
                <span key={part.sku} className="mr-2 inline-block">
                  <span className="font-mono text-xs">{part.sku}</span> {humanize(part.status)}
                </span>
              ))}
        </Field>
        <Field label="Purchase order">
          {po ? (
            <>
              {po.id} · {po.vendor_name} · {formatLkr(po.total_lkr)} · <strong>{humanize(po.status)}</strong>
              {po.warranty_claim && <span className="ml-1 text-xs text-emerald-700">(warranty claim)</span>}
              {approval?.required && (
                <p className="text-xs text-slate-600">
                  Manager approval: {approval.decision ? humanize(approval.decision) : "pending"}
                  {approval.comment ? `: “${approval.comment}”` : ""}
                </p>
              )}
            </>
          ) : (
            "None"
          )}
        </Field>
        <Field label="Technician visit">
          {workOrder ? (
            <>
              {workOrder.technician_name} ({workOrder.technician_id}) · {formatDateTime(workOrder.scheduled_start)} ·{" "}
              {workOrder.est_hours} h
              {summary.sla_risk && <span className="ml-2 rounded bg-rose-50 px-1.5 text-xs text-rose-700">SLA at risk</span>}
              <p className="text-xs text-slate-600">{workOrder.reason}</p>
            </>
          ) : (
            "Not booked"
          )}
        </Field>
        <Field label="Ticket">{run.ticket_id ?? "No ticket"}</Field>
        {summary.customer_message && (
          <div className="sm:col-span-2">
            <Field label="Message to the customer">
              <p className="whitespace-pre-line rounded-md bg-slate-50 p-3 text-slate-700">{summary.customer_message}</p>
            </Field>
          </div>
        )}
        {state.ticket_notes && state.ticket_notes.length > 0 && (
          <div className="sm:col-span-2">
            <Field label="Ticket notes (from business rules)">
              <ul className="list-disc pl-5 text-slate-700">
                {state.ticket_notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </Field>
          </div>
        )}
        {summary.errors.length > 0 && (
          <div className="sm:col-span-2">
            <Field label="Needs attention">
              <ul className="list-disc pl-5 text-rose-700">
                {summary.errors.map((error) => (
                  <li key={error}>{error}</li>
                ))}
              </ul>
            </Field>
          </div>
        )}
      </dl>
    </Card>
  );
}
