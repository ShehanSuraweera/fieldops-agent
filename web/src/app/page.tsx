"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { demoClock, demoDate } from "@/lib/format";
import { PRESETS } from "@/lib/presets";
import { Card, ErrorNote, Spinner } from "@/components/ui";

export default function NewTicketPage() {
  const router = useRouter();
  const [email, setEmail] = useState(PRESETS[0].customer_email);
  const [text, setText] = useState(PRESETS[0].text);
  const [selected, setSelected] = useState(0);
  const [useDemoClock, setUseDemoClock] = useState(true);
  const date = demoDate();

  const start = useMutation({
    mutationFn: () =>
      api.startRun({ customer_email: email.trim(), text: text.trim(), ...(useDemoClock ? { now: demoClock(date) } : {}) }),
    onSuccess: (run) => router.push(`/runs/${run.run_id}`),
  });

  const reset = useMutation({
    mutationFn: async () => {
      const response = await fetch("/api/demo/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ today: date }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body?.error?.message ?? "Reset failed");
      return body as { seeded_for: string };
    },
  });

  function choose(index: number) {
    setSelected(index);
    setEmail(PRESETS[index].customer_email);
    setText(PRESETS[index].text);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">New ticket</h1>
        <p className="mt-1 text-sm text-slate-600">
          Send a customer complaint to the agent. It identifies the asset, diagnoses the fault, applies the business
          rules, orders parts and books a technician.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-5">
        <div className="space-y-3 lg:col-span-2">
          <h2 className="text-sm font-semibold text-slate-700">Example complaints</h2>
          {PRESETS.map((preset, index) => (
            <button
              key={preset.title}
              type="button"
              onClick={() => choose(index)}
              className={`block w-full rounded-lg border p-3 text-left text-sm transition ${
                selected === index ? "border-sky-500 bg-sky-50" : "border-slate-200 bg-white hover:border-slate-300"
              }`}
            >
              <span className="font-medium">{preset.title}</span>
              <span className="mt-0.5 block text-xs text-slate-600">{preset.expect}</span>
            </button>
          ))}
        </div>

        <div className="space-y-4 lg:col-span-3">
          <Card title="Complaint">
            <form
              className="space-y-4"
              onSubmit={(event) => {
                event.preventDefault();
                start.mutate();
              }}
            >
              <label className="block text-sm">
                <span className="text-slate-700">Sender email</span>
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(event) => {
                    setEmail(event.target.value);
                    setSelected(-1);
                  }}
                  className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 focus:border-sky-500 focus:outline-none"
                />
              </label>
              <label className="block text-sm">
                <span className="text-slate-700">Message</span>
                <textarea
                  required
                  rows={5}
                  value={text}
                  onChange={(event) => {
                    setText(event.target.value);
                    setSelected(-1);
                  }}
                  className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 focus:border-sky-500 focus:outline-none"
                />
              </label>
              <label className="flex items-start gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={useDemoClock}
                  onChange={(event) => setUseDemoClock(event.target.checked)}
                  className="mt-1"
                />
                <span>
                  Demo clock: run as if it is <strong>{date} 09:00</strong> Colombo time, so the presets behave the same
                  at any hour. Pair it with “Reset demo data”.
                </span>
              </label>
              <ErrorNote error={start.error} />
              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="submit"
                  disabled={start.isPending}
                  className="inline-flex items-center gap-2 rounded-md bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-700 disabled:opacity-60"
                >
                  {start.isPending && <Spinner />} Start run
                </button>
              </div>
            </form>
          </Card>

          <Card title="Demo data">
            <p className="text-sm text-slate-600">
              Reseed the mock CRM, FSM and ERP for <strong>{date}</strong>. This clears tickets, bookings, reservations
              and purchase orders. Agent runs are kept.
            </p>
            <div className="mt-3 flex items-center gap-3">
              <button
                type="button"
                onClick={() => reset.mutate()}
                disabled={reset.isPending}
                className="inline-flex items-center gap-2 rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-60"
              >
                {reset.isPending && <Spinner />} Reset demo data
              </button>
              {reset.isSuccess && <span className="text-sm text-emerald-700">Seeded for {reset.data.seeded_for}.</span>}
            </div>
            <div className="mt-2">
              <ErrorNote error={reset.error} />
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
