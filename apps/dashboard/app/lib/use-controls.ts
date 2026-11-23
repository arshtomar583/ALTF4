"use client";

import { useState } from "react";

import { approvable, type IncidentState, pendingJudge } from "./incident-state";
import { post } from "./use-incident";

type Control = { enabled: boolean; label: string; busy: boolean; hint: string | null; problem: string | null; run: () => void };
export type Controls = { breakIt: Control; judge: Control; approve: Control };

/** Everything a judge can do, with the reason whenever it is not allowed. */
export function useControls(state: IncidentState, incidentOpen: boolean | null, recorded: boolean): Controls {
  const [sending, setSending] = useState<string | null>(null);
  const [problems, setProblems] = useState<Record<string, string | null>>({});

  async function send(name: string, path: string, body?: object) {
    if (sending) return;
    setSending(name);
    const problem = await post(path, body);
    setProblems((before) => ({ ...before, [name]: problem }));
    setSending(null);
  }

  // The brain knows whether an incident is open; the story is only a fallback.
  const open = incidentOpen ?? state.phase !== "healthy";
  const broken = state.phase === "down";
  const waiting = pendingJudge(state);
  const afterFix = state.phase === "verified";
  const candidate = approvable(state);
  const releasing = state.phase === "releasing" || sending === "approve";
  const notLive = "Not live, so controls are off";

  return {
    breakIt: {
      enabled: !recorded && !open && !broken && sending === null,
      label: "BREAK",
      busy: sending === "break",
      hint: recorded ? notLive : open || broken ? "An incident is open" : sending === "break" ? "Deploying the bad commit…" : "Deploys a bad commit to the live shop",
      problem: problems.break ?? null,
      run: () => void send("break", "/chaos/break", { fault: "bad_commit" }),
    },
    judge: {
      enabled: !recorded && afterFix && !waiting && sending === null,
      label: "Judge: submit a bad candidate",
      busy: waiting !== null,
      hint: recorded ? null : waiting ? "Judge's candidate queued → verifying…" : afterFix ? "Must be rejected" : "Available after a verified fix",
      problem: problems.judge ?? null,
      run: () => void send("judge", "/chaos/bad-candidate"),
    },
    approve: {
      enabled: !recorded && candidate !== null && afterFix && !state.closed && !releasing,
      label: releasing ? "Releasing…" : state.phase === "recovered" ? "Released" : "Approve release",
      busy: releasing,
      hint: null,
      problem: problems.approve ?? state.releaseProblem,
      run: () => {
        if (candidate && state.incidentId) {
          void send("approve", `/incidents/${state.incidentId}/approve`, { candidate_id: candidate.id });
        }
      },
    },
  };
}
