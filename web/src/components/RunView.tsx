"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, eventsUrl, type RunStatus, type Step } from "@/lib/api";
import { formatLkr } from "@/lib/format";
import { ApprovalPanel } from "@/components/ApprovalPanel";
import { SummaryCard } from "@/components/SummaryCard";
import { Timeline } from "@/components/Timeline";
import { Card, ErrorNote, StatusBadge } from "@/components/ui";

export function RunView({ runId }: { runId: string }) {
  const queryClient = useQueryClient();
  const [steps, setSteps] = useState<Step[]>([]);
  const [liveStatus, setLiveStatus] = useState<RunStatus>("running");
  const [stream, setStream] = useState(0); // bump to reconnect, e.g. after an approval
  const lastSeq = useRef(0);

  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.run(runId),
    refetchInterval: (query) => (query.state.data?.status === "running" ? 3_000 : false),
  });

  // Live step stream (Server-Sent Events). The server ends the stream with a `status`
  // event when the run finishes or pauses; we close then, so the browser does not reconnect.
  useEffect(() => {
    const source = new EventSource(eventsUrl(runId, lastSeq.current));
    source.addEventListener("step", (event) => {
      const step = JSON.parse((event as MessageEvent).data) as Step;
      if (step.seq <= lastSeq.current) return;
      lastSeq.current = step.seq;
      setSteps((previous) => [...previous, step]);
    });
    source.addEventListener("status", (event) => {
      const { status } = JSON.parse((event as MessageEvent).data) as { status: RunStatus };
      setLiveStatus(status);
      source.close();
      queryClient.invalidateQueries({ queryKey: ["run", runId] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      queryClient.invalidateQueries({ queryKey: ["metrics"] });
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
    });
    return () => source.close();
  }, [runId, stream, queryClient]);

  const data = run.data;
  const status = liveStatus === "running" && data && data.status !== "running" ? data.status : liveStatus;
  const live = status === "running";
  const po = data?.final_state?.purchase_order;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link href="/runs" className="text-sm text-sky-700 hover:underline">
            ← All runs
          </Link>
          <h1 className="mt-1 text-2xl font-semibold">
            Run <span className="font-mono text-xl">{runId}</span>
          </h1>
          {data && (
            <p className="mt-1 text-sm text-slate-600">
              From <strong>{data.customer_email}</strong>
              {data.final_state?.customer && ` (${data.final_state.customer.name}, ${data.final_state.customer.contract_tier})`}
              {data.ticket_id && ` · ticket ${data.ticket_id}`} · tokens {data.input_tokens + data.output_tokens}
            </p>
          )}
        </div>
        <StatusBadge status={status} />
      </div>
      <ErrorNote error={run.error} />

      {data && (
        <Card title="Complaint">
          <p className="text-sm text-slate-700">“{data.raw_text}”</p>
        </Card>
      )}

      {status === "awaiting_approval" && po && (
        <Card title="Manager approval needed">
          <p className="mb-3 text-sm text-slate-700">
            {po.id} from <strong>{po.vendor_name}</strong> for <strong>{formatLkr(po.total_lkr)}</strong> is above the
            approval threshold. {po.reason}
          </p>
          <ApprovalPanel
            runId={runId}
            onDecided={() => {
              setLiveStatus("running");
              setStream((n) => n + 1);
            }}
          />
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <Card title={`Steps (${steps.length})`} actions={live ? <span className="text-xs text-sky-700">live</span> : null}>
            <Timeline steps={steps} live={live} />
          </Card>
        </div>
        <div className="lg:col-span-2">
          {data && data.status !== "running" ? (
            <SummaryCard run={data} />
          ) : (
            <Card title="Outcome">
              <p className="text-sm text-slate-500">The summary appears when the run finishes.</p>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
