"use client";

import { memo, useEffect, useState } from "react";

import type { Beat } from "../lib/incident-state";
import { Check, Warn } from "./icons";

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 border-l border-line px-5 first:border-l-0">
      <dt className="label">{label}</dt>
      <dd className="mt-1 text-title leading-snug font-medium tabular-nums">{value}</dd>
    </div>
  );
}

/** The calm state. Every fact is read from the probes and events themselves. */
export const Empty = memo(function Empty({ beats, slot, revision, last, healthy }: {
  beats: Beat[];
  slot: string | null;
  revision: string | null;
  last: string | null;
  healthy: boolean;
}) {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, []);
  const recent = now === null ? [] : beats.filter((beat) => now - beat.at <= 60_000);
  const passed = recent.filter((beat) => beat.ok).length;
  return (
    <div className="grid h-full place-content-center gap-8 rounded-panel border border-line bg-panel p-8 text-center">
      <p className="text-5xl leading-tight font-semibold">
        {healthy ? (
          <><Check className="h-10 w-10 text-ok" /> Checkout healthy</>
        ) : (
          <span className="text-text-2"><Warn className="h-10 w-10" /> No healthy checkout signal</span>
        )}
      </p>
      <dl className="grid grid-cols-3">
        <Fact label="Probes passed, last 60s" value={recent.length === 0 ? "None received" : `${passed} of ${recent.length}`} />
        <Fact label="Live version" value={slot && revision ? `Slot ${slot.toUpperCase()} @ ${revision.slice(0, 7)}` : "Not known yet"} />
        <Fact label="Last incident" value={last ?? "None yet"} />
      </dl>
      <p className="text-text-2">{healthy ? "Press BREAK to simulate a bad deploy" : "Waiting for the brain to report checkouts"}</p>
    </div>
  );
});
