"use client";

import { useEffect, useState } from "react";

type ProbeEvent = {
  id: number;
  type: "checkout.probe";
  occurred_at: string;
  data: {
    ok: boolean;
    slot: string | null;
    status_code: number | null;
    latency_ms: number;
    revision: string | null;
  };
};

const recentLimit = 24;

export function CheckoutMonitor() {
  const [probes, setProbes] = useState<ProbeEvent[]>([]);
  const [connection, setConnection] = useState("Connecting");

  useEffect(() => {
    const api = process.env.NEXT_PUBLIC_BRAIN_URL ?? "http://127.0.0.1:8000";
    const stream = new EventSource(api + "/events");
    stream.onopen = () => setConnection("Live");
    stream.onerror = () => setConnection("Reconnecting");
    stream.onmessage = ({ data }) => {
      const event: ProbeEvent = JSON.parse(data);
      if (event.type === "checkout.probe") {
        setProbes((previous) => [...previous, event].slice(-recentLimit));
      }
    };
    return () => stream.close();
  }, []);

  const latest = probes.at(-1);
  const result = latest ? (latest.data.ok ? "Passing" : "Failing") : "Waiting for a probe";
  const details = [
    { label: "Active slot", value: latest?.data.slot?.toUpperCase() },
    { label: "Response", value: latest?.data.status_code },
    { label: "Latency", value: latest ? latest.data.latency_ms + " ms" : null },
  ];

  return (
    <section aria-labelledby="checkout-heading" className="rounded-2xl border border-slate-700 bg-[#141d2b] p-4 sm:p-8">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="checkout-heading" className="text-sm font-medium text-slate-300">Latest checkout</h2>
          <p aria-live="polite" className="mt-2 text-2xl font-semibold sm:text-3xl">{result}</p>
        </div>
        <p role="status" className="rounded-full border border-slate-600 px-3 py-1 text-xs text-slate-200">
          {connection}
        </p>
      </div>
      <dl className="mt-5 grid grid-cols-3 gap-x-3 gap-y-4 border-y border-slate-700 py-4 text-sm sm:mt-7 sm:gap-5 sm:py-5">
        {details.map(({ label, value }) => (
          <div key={label}>
            <dt className="text-slate-300">{label}</dt>
            <dd className="mt-1 font-mono">{value ?? "–"}</dd>
          </div>
        ))}
        <div className="col-span-full">
          <dt className="text-slate-300">Revision</dt>
          <dd className="mt-1 font-mono wrap-anywhere">{latest?.data.revision ?? "–"}</dd>
        </div>
      </dl>
      <h3 className="mt-6 text-sm font-medium text-slate-300">Recent probes</h3>
      {probes.length === 0 ? (
        <p className="mt-2 text-sm text-slate-400">Waiting for ShopLite checkout results.</p>
      ) : (
        <ul className="mt-3 flex flex-wrap gap-2">
          {probes.map((probe) => {
            const label = (probe.data.ok ? "Passed" : "Failed") + " at " + new Date(probe.occurred_at).toLocaleTimeString();
            const color = probe.data.ok ? "bg-emerald-400" : "bg-red-400";
            return (
              <li key={probe.id} title={label} className={"h-3 w-3 rounded-full " + color}>
                <span className="sr-only">{label}</span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
