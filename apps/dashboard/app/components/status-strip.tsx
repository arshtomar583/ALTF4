"use client";

import { memo, useEffect, useState } from "react";

import type { Phase } from "../lib/incident-state";
import type { Connection } from "../lib/use-incident";
import { Check, Clock, Cross, Page, Warn } from "./icons";

type Mood = "down" | "fixing" | "ok";
type PhaseView = { text: string; mood: Mood | null; Icon: typeof Check };

const PHASES: Record<Phase, PhaseView> = {
  healthy: { text: "Healthy", mood: null, Icon: Check },
  down: { text: "Checkout down", mood: "down", Icon: Cross },
  fixing: { text: "Restored — fixing", mood: "fixing", Icon: Warn },
  verified: { text: "Fix verified", mood: "ok", Icon: Check },
  releasing: { text: "Releasing", mood: "ok", Icon: Clock },
  recovered: { text: "Recovered", mood: "ok", Icon: Check },
  escalated: { text: "Escalated", mood: "fixing", Icon: Warn },
};

const NO_SIGNAL: PhaseView = { text: "No signal", mood: null, Icon: Warn };
const FAILING: PhaseView = { text: "Checkout failing", mood: "down", Icon: Cross };
const FILL: Record<Mood, string> = { down: "bg-down", fixing: "bg-fixing", ok: "bg-ok" };
const PILL: Record<Mood, string> = {
  down: "border-down text-down",
  fixing: "border-fixing text-fixing",
  ok: "border-ok text-ok",
};
const MOODS: Mood[] = ["down", "fixing", "ok"];

function duration(start: number | null, end: number | null, now: number): string | null {
  if (start === null) return null;
  const seconds = Math.max(0, ((end ?? now) - start) / 1000);
  if (seconds < 100) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}

/** One figure. Without one it says so in small text instead of filling the strip with a dash. */
function Metric({ label, value, note, none }: { label: string; value: string | null; note: string | null; none: string }) {
  return (
    <div className="min-w-0">
      <dt className="label whitespace-nowrap">{label}</dt>
      <dd className="flex h-[4.4rem] items-center gap-2.5 whitespace-nowrap">
        {value === null ? (
          <span className="text-text-3">{none}</span>
        ) : (
          <span className="text-timer leading-[1.1] font-medium tabular-nums">
            <span key={label === "Failed checkouts" ? value : undefined} className={label === "Failed checkouts" ? "bump inline-block" : undefined}>{value}</span>
          </span>
        )}
        {value !== null && note ? (
          <span className="max-w-16 text-meta leading-tight whitespace-normal text-text-2">
            <Check /> {note}
          </span>
        ) : null}
      </dd>
    </div>
  );
}

export const StatusStrip = memo(function StatusStrip(props: {
  phase: Phase;
  closed: boolean;
  openedAt: number | null;
  restoredAt: number | null;
  verifiedAt: number | null;
  failed: number;
  connection: Connection;
  signal: boolean;
  lastOk: boolean;
  canReport: boolean;
  onReport: () => void;
}) {
  const { phase, closed, openedAt, restoredAt, verifiedAt, failed, connection, signal, lastOk } = props;
  const [now, setNow] = useState(0);
  // An escalated incident has no verified fix coming, so its clock shows no number.
  const abandoned = phase === "escalated" && verifiedAt === null;
  const running = openedAt !== null && verifiedAt === null && !abandoned;
  useEffect(() => {
    if (!running) return;
    const tick = () => setNow(Date.now());
    tick();
    const timer = window.setInterval(tick, 100);
    return () => window.clearInterval(timer);
  }, [running]);

  const { text, mood, Icon } = !signal ? NO_SIGNAL : phase === "healthy" && !lastOk ? FAILING : PHASES[phase];
  const live = closed ? null : mood;
  const none = phase === "down" ? "Detecting…" : "No incident yet";
  const note = (done: boolean) => (!done ? null : closed ? "last incident" : "final");
  return (
    <header className="relative grid h-25.5 shrink-0 grid-cols-[19.5rem_minmax(0,1fr)_9.5rem] items-center gap-x-6 px-5">
      <div className="min-w-0">
        <div className="flex items-baseline gap-3">
          <h1 className="text-base font-semibold tracking-[0.18em]">PHOENIX</h1>
          <p className={`text-meta ${connection === "Reconnecting" ? "text-fixing" : "text-text-2"}`}>
            {connection === "Reconnecting" ? <Warn /> : null} {connection}
          </p>
        </div>
        <p className="relative mt-1.5 inline-flex rounded-control border border-line text-state leading-tight font-semibold">
          <span className={`inline-flex items-center gap-2 px-3 py-1 whitespace-nowrap ${signal ? "text-text" : "text-text-2"}`}>
            <Icon className="h-6 w-6" /> {text}
          </span>
          {MOODS.map((name) => (
            <span
              key={name}
              aria-hidden
              data-on={live === name}
              data-alarm={name === "down"}
              data-settling={closed}
              className={`mood absolute -inset-px inline-flex items-center gap-2 rounded-control border bg-panel px-3 py-1 whitespace-nowrap ${PILL[name]}`}
            >
              <Icon className="h-6 w-6" /> {text}
            </span>
          ))}
        </p>
      </div>
      <dl className="grid min-w-0 grid-cols-3 gap-x-6">
        <Metric label="Time to restore" value={duration(openedAt, restoredAt, now)} note={note(restoredAt !== null)} none={none} />
        <Metric
          label="Time to verified fix"
          value={abandoned ? null : duration(openedAt, verifiedAt, now)}
          note={note(verifiedAt !== null)}
          none={abandoned ? "Escalated" : none}
        />
        <Metric
          label="Failed checkouts"
          value={openedAt === null && failed === 0 ? null : String(failed)}
          note={note(restoredAt !== null)}
          none={none}
        />
      </dl>
      <button
        type="button"
        disabled={!props.canReport}
        onClick={props.onReport}
        className="control inline-flex h-11 items-center justify-center gap-2 rounded-control border border-line text-meta text-text disabled:text-text-3"
      >
        <Page /> {props.canReport ? "View report" : "No report yet"}
      </button>
      <div aria-hidden className="absolute inset-x-0 bottom-0 h-px bg-line" />
      {MOODS.map((name) => (
        <div
          key={name}
          aria-hidden
          data-on={live === name}
          data-alarm={name === "down"}
          data-settling={closed}
          className={`mood absolute inset-x-0 bottom-0 h-0.5 ${FILL[name]}`}
        />
      ))}
    </header>
  );
});
