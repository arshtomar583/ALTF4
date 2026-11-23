"use client";

import { useEffect, useState } from "react";

import type { IncidentState, Phase } from "../lib/incident-state";
import type { Connection } from "../lib/use-incident";

const PHASES: Record<Phase, { icon: string; text: string; tone: string }> = {
  healthy: { icon: "✓", text: "Healthy", tone: "text-ink" },
  down: { icon: "✗", text: "Checkout down", tone: "text-bad" },
  fixing: { icon: "⚠", text: "Restored, fixing", tone: "text-busy" },
  verified: { icon: "✓", text: "Fix verified", tone: "text-good" },
  releasing: { icon: "⚠", text: "Releasing", tone: "text-busy" },
  recovered: { icon: "✓", text: "Recovered", tone: "text-good" },
  escalated: { icon: "⚠", text: "Escalated", tone: "text-busy" },
};

function duration(start: number | null, end: number | null, now: number): string {
  if (start === null) return "–";
  const seconds = Math.max(0, ((end ?? now) - start) / 1000);
  if (seconds < 100) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}

function Metric({ label, value, tone = "text-ink" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-sm text-dim">{label}</dt>
      <dd className={`font-mono text-5xl leading-none font-medium tabular-nums ${tone}`}>{value}</dd>
    </div>
  );
}

export function StatusStrip({ state, connection }: { state: IncidentState; connection: Connection }) {
  const [now, setNow] = useState(0);
  // An escalated incident has no verified fix coming, so its clock shows no number.
  const running = state.openedAt !== null && state.verifiedAt === null && state.phase !== "escalated";
  useEffect(() => {
    if (!running) return;
    const tick = () => setNow(Date.now());
    tick();
    const timer = window.setInterval(tick, 100);
    return () => window.clearInterval(timer);
  }, [running]);

  const phase = PHASES[state.phase];
  const stopped = state.phase === "escalated" && state.verifiedAt === null;
  return (
    <header className="grid h-24 shrink-0 grid-cols-[minmax(0,1.35fr)_repeat(3,minmax(0,1fr))_minmax(0,0.85fr)] items-center gap-5 border-b border-line px-5">
      <div>
        <h1 className="text-sm font-semibold tracking-[0.18em] text-dim">PHOENIX</h1>
        <p aria-live="polite" className={`mt-1 text-2xl font-semibold whitespace-nowrap ${phase.tone}`}>
          <span aria-hidden>{phase.icon}</span> {phase.text}
        </p>
        <p role="status" className={`text-sm ${connection === "Reconnecting" ? "text-busy" : "text-dim"}`}>
          {connection === "Reconnecting" ? "⚠ Reconnecting" : connection}
        </p>
      </div>
      <dl className="contents">
        <Metric
          label="Time to restore"
          value={duration(state.openedAt, state.restoredAt, now)}
          tone={state.openedAt === null ? "text-dim" : state.restoredAt === null ? "text-bad" : "text-ink"}
        />
        <Metric
          label="Time to verified fix"
          value={stopped ? "–" : duration(state.openedAt, state.verifiedAt, now)}
          tone={state.verifiedAt !== null ? "text-good" : state.restoredAt !== null && !stopped ? "text-busy" : "text-dim"}
        />
        <Metric
          label="Failed checkouts"
          value={String(state.failedCheckouts)}
          tone={state.phase === "down" ? "text-bad" : state.failedCheckouts ? "text-ink" : "text-dim"}
        />
        <div className="min-w-0">
          <dt className="text-sm text-dim">Live slot</dt>
          <dd className="truncate font-mono text-2xl leading-tight">{state.slot?.toUpperCase() ?? "–"}</dd>
          <dd className="truncate font-mono text-sm text-dim">{state.revision?.slice(0, 7) ?? "no revision yet"}</dd>
        </div>
      </dl>
    </header>
  );
}
