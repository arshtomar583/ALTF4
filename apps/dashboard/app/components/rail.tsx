"use client";

import { useState } from "react";

import type { IncidentState } from "../lib/incident-state";
import { post } from "../lib/use-incident";

export function Rail({ state, incidentOpen, recorded, onReport }: {
  state: IncidentState;
  incidentOpen: boolean | null;
  recorded: boolean;
  onReport: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function send(path: string, body?: object) {
    setBusy(true);
    setProblem(await post(path, body));
    setBusy(false);
  }

  // The brain knows whether an incident is open; the story is only a fallback.
  const open = incidentOpen ?? state.phase !== "healthy";
  const canBreak = !recorded && !busy && !open && state.phase !== "down";
  const canChallenge = !recorded && !busy && ["fixing", "verified", "escalated"].includes(state.phase);

  return (
    <aside aria-label="Judge controls" className="flex min-h-0 flex-col items-center gap-3 rounded-lg border border-line bg-panel px-3 py-4">
      <button
        type="button"
        disabled={!canBreak}
        onClick={() => send("/chaos/break", { fault: "bad_commit" })}
        className={`h-32 w-32 shrink-0 rounded-full border-4 text-2xl font-bold tracking-wide ${
          canBreak ? "border-bad text-bad hover:bg-bad hover:text-ground" : "border-line text-dim"
        }`}
      >
        BREAK
      </button>
      <p className="text-center text-sm text-dim">
        {recorded
          ? "Recorded story, controls are off"
          : open
            ? "An incident is open"
            : "Deploys a bad commit to the live shop"}
      </p>
      <button
        type="button"
        disabled={!canChallenge}
        onClick={() => send("/chaos/bad-candidate")}
        className={`w-full rounded-md border px-2 py-2 text-sm ${canChallenge ? "border-dim text-ink" : "border-line text-dim"}`}
      >
        Submit a bad candidate (judge)
      </button>
      <div className="min-h-0 w-full flex-1 overflow-y-auto">
        {problem ? <p role="alert" className="text-sm wrap-anywhere text-busy">⚠ {problem}</p> : null}
      </div>
      <button
        type="button"
        disabled={!state.report}
        onClick={onReport}
        className={`w-full rounded-md border px-2 py-2 text-sm ${state.report ? "border-dim text-ink" : "border-line text-dim"}`}
      >
        {state.report ? "View report" : "No report yet"}
      </button>
    </aside>
  );
}
