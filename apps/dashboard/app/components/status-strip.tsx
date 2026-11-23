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

const FILL: Record<Mood, string> = { down: "bg-down", fixing: "bg-fixing", ok: "bg-ok" };
const PILL: Record<Mood, string> = {
  down: "border-down text-down",
  fixing: "border-fixing text-fixing",
  ok: "border-ok text-ok",
};
const MOODS: Mood[] = ["down", "fixing", "ok"];

function duration(start: number | null, end: number | null, now: number): string {
  if (start === null) return "—";
  const seconds = Math.max(0, ((end ?? now) - start) / 1000);
  if (seconds < 100) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}

function Timer({ label, value, final }: { label: string; value: string; final: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="label flex items-center gap-1.5 whitespace-nowrap">
        {label}
        {final ? (
          <span className="inline-flex items-center gap-1 tracking-normal text-text-2 normal-case">
            <Check /> final
          </span>
        ) : null}
      </dt>
      <dd className={`tnum text-timer leading-[1.1] font-medium whitespace-nowrap ${value === "—" ? "text-text-3" : "text-text"}`}>
        {value}
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
  canReport: boolean;
  onReport: () => void;
  onHelp: () => void;
}) {
  const { phase, closed, openedAt, restoredAt, verifiedAt, failed, connection } = props;
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

  const { text, mood, Icon } = PHASES[phase];
  const live = closed ? null : mood;
  return (
    <header className="relative grid h-25.5 shrink-0 grid-cols-[minmax(0,1fr)_13.5rem_15.5rem_8.5rem_9.5rem] items-center gap-x-4 px-5">
      <div className="min-w-0">
        <div className="flex items-baseline gap-3">
          <h1 className="text-base font-semibold tracking-[0.18em]">PHOENIX</h1>
          <p className={`text-meta ${connection === "Reconnecting" ? "text-fixing" : "text-text-2"}`}>
            {connection === "Reconnecting" ? <Warn /> : null} {connection}
          </p>
        </div>
        <p className="relative mt-1.5 inline-flex rounded-control border border-line text-state leading-tight font-semibold">
          <span className="inline-flex items-center gap-2 px-3 py-1 whitespace-nowrap text-text">
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
      <dl className="contents">
        <Timer label="Time to restore" value={duration(openedAt, restoredAt, now)} final={restoredAt !== null} />
        <Timer label="Time to verified fix" value={abandoned ? "—" : duration(openedAt, verifiedAt, now)} final={verifiedAt !== null} />
        <div className="min-w-0">
          <dt className="label flex items-center gap-1.5 whitespace-nowrap">
            Failed
            {restoredAt !== null ? (
              <span className="inline-flex items-center gap-1 tracking-normal text-text-2 normal-case"><Check /> final</span>
            ) : null}
          </dt>
          <dd className={`tnum text-timer leading-[1.1] font-medium ${failed ? "text-text" : "text-text-3"}`}>
            <span key={failed} className={`inline-block ${failed ? "bump" : ""}`}>{failed}</span>
            <span className="sr-only"> failed checkouts</span>
          </dd>
        </div>
      </dl>
      <div className="flex flex-col gap-1">
        <button
          type="button"
          disabled={!props.canReport}
          onClick={props.onReport}
          className="control inline-flex h-11 items-center gap-2 rounded-control border border-line px-3 text-meta text-text disabled:text-text-3"
        >
          <Page /> {props.canReport ? "View report" : "No report yet"}
        </button>
        <button type="button" onClick={props.onHelp} className="control h-11 rounded-control px-3 text-meta text-text-2">
          <kbd className="tnum">?</kbd> Shortcuts
        </button>
      </div>
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
