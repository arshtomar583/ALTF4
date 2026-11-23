import { memo, type ReactNode } from "react";

import type { IncidentState } from "../lib/incident-state";
import { Check, Cross, Download, Page, Warn } from "./icons";

export const STAGES = ["Restore", "Diagnose", "Verify", "Release"] as const;

/** The stage an incident has reached. */
export function stageOf(state: IncidentState): number {
  if (state.phase === "recovered" || state.phase === "releasing") return 3;
  if (state.phase === "escalated" || state.candidates.length === 0) return state.restoredAt === null ? 0 : 1;
  return 2;
}

export const Stepper = memo(function Stepper({ reached, shown, done, onPick }: {
  reached: number;
  shown: number;
  done: boolean;
  onPick: (stage: number) => void;
}) {
  return (
    <nav aria-label="Incident stages" className="grid grid-cols-4 gap-1 rounded-panel border border-line bg-panel p-1">
      {STAGES.map((name, index) => {
        const finished = index < reached || done;
        return (
          <button
            key={name}
            type="button"
            disabled={index > reached}
            aria-current={index === shown ? "step" : undefined}
            onClick={() => onPick(index)}
            className={`control flex min-h-11 flex-col items-center justify-center gap-x-2.5 gap-y-0.5 rounded-control py-1 text-meta sm:flex-row sm:text-base font-medium disabled:text-text-3 ${
              index === shown ? "bg-panel-2 text-text" : "text-text-2"
            }`}
          >
            <span className={`grid h-6 w-6 place-items-center rounded-full border text-meta tabular-nums ${finished ? "border-text bg-text text-bg" : index === reached ? "border-text text-text" : "border-line"}`}>
              {finished ? <Check className="h-3.5 w-3.5" /> : index + 1}
            </span>
            {name}
          </button>
        );
      })}
    </nav>
  );
});

function Stage({ icon, title, text, children }: { icon: ReactNode; title: string; text: string; children?: ReactNode }) {
  return (
    <section className="appear grid h-full place-content-center justify-items-center gap-4 rounded-panel border border-line bg-panel p-5 text-center sm:p-8">
      <span className="grid h-16 w-16 place-items-center rounded-full border border-line bg-panel-2 text-state">{icon}</span>
      <h2 className="text-3xl leading-tight sm:text-4xl font-semibold text-balance">{title}</h2>
      <p className="max-w-[46ch] text-title leading-snug text-pretty text-text-2">{text}</p>
      {children}
    </section>
  );
}

export function RestoreStage({ state }: { state: IncidentState }) {
  const { openedAt, restoredAt, failedCheckouts: failed, slot, revision } = state;
  const carts = `${failed} checkout${failed === 1 ? "" : "s"} failed`;
  if (restoredAt === null) {
    return (
      <Stage
        icon={<Cross className="h-7 w-7 text-down" />}
        title="Checkout is down"
        text={openedAt === null ? `${carts} so far. Three failures in a row open an incident.` : "Switching back to the last healthy version."}
      />
    );
  }
  const seconds = openedAt === null ? null : ((restoredAt - openedAt) / 1000).toFixed(1);
  return (
    <Stage
      icon={<Check className="h-7 w-7" />}
      title={seconds === null ? "Service restored" : `Service restored in ${seconds}s`}
      text={`${carts}. Live on slot ${slot?.toUpperCase() ?? "—"} at ${revision?.slice(0, 7) ?? "—"}.`}
    />
  );
}

export function ReleaseStage({ state, onReport, onDownload }: {
  state: IncidentState;
  onReport: () => void;
  onDownload: () => void;
}) {
  if (state.phase === "releasing") {
    return (
      <Stage icon={<Warn className="h-7 w-7" />} title="Releasing the verified fix" text="Checking it on the idle slot before any customer reaches it.">
        <span aria-hidden className="relative h-1 w-64 overflow-hidden rounded-full bg-line"><span className="progress absolute inset-y-0 left-0 w-1/3 bg-text" /></span>
      </Stage>
    );
  }
  return (
    <Stage
      icon={<Check className="h-7 w-7 text-ok" />}
      title="Verified fix is live"
      text={`Live on slot ${state.slot?.toUpperCase() ?? "—"} at ${state.revision?.slice(0, 7) ?? "—"}.`}
    >
      <div className="flex flex-wrap justify-center gap-3">
        <button type="button" disabled={!state.report} onClick={onReport} className="control inline-flex min-h-11 items-center gap-2 rounded-control bg-text px-5 font-medium text-bg disabled:bg-panel-2 disabled:text-text-3">
          <Page /> {state.report ? "View report" : "Writing the report…"}
        </button>
        <button type="button" disabled={!state.report} onClick={onDownload} className="control inline-flex min-h-11 items-center gap-2 rounded-control border border-line px-5 font-medium disabled:text-text-3">
          <Download /> Download
        </button>
      </div>
    </Stage>
  );
}
