"use client";

import { useState } from "react";

import { approvable, type Candidate, type IncidentState } from "../lib/incident-state";
import { post } from "../lib/use-incident";
import { Panel, Waiting } from "./ui";

function Mark({ ok, pending = false }: { ok: boolean | null; pending?: boolean }) {
  if (ok === null) return <span className="text-dim">{pending ? "…" : "–"}</span>;
  return (
    <span className={ok ? "text-good" : "text-bad"}>
      <span aria-hidden>{ok ? "✓" : "✗"}</span> <span className="text-sm">{ok ? "pass" : "fail"}</span>
    </span>
  );
}

function Stamp({ candidate }: { candidate: Candidate }) {
  const result = candidate.verification;
  if (!result) return <p className="waiting text-dim">{candidate.label}: being verified…</p>;
  const ok = result.verdict === "VERIFIED";
  return (
    <div key={candidate.id} className="stamp min-h-0">
      <p className={`inline-block border-2 px-2 py-0.5 text-xl font-bold tracking-wide ${ok ? "border-good text-good" : "border-bad text-bad"}`}>
        {ok ? "✓" : "✗"} {result.verdict}
      </p>
      <p className="mt-1 line-clamp-2 text-sm leading-snug">
        <strong>{candidate.label}:</strong> {result.reason}
      </p>
    </div>
  );
}

export function Verification({ state, active, recorded }: { state: IncidentState; active: boolean; recorded: boolean }) {
  const [sending, setSending] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const candidate = approvable(state);
  // The newest candidates matter most, and four columns fit the projector.
  const shown = state.candidates.slice(-4);
  const latest = state.candidates.findLast((item) => item.verification) ?? state.candidates.at(-1);
  const first = state.candidates.find((item) => item.verification)?.verification ?? null;
  const releasing = state.phase === "releasing" || sending === candidate?.id;
  const ready = candidate !== null && state.phase === "verified" && !releasing && !recorded;

  async function approve() {
    if (!candidate || !state.incidentId) return;
    setSending(candidate.id);
    setProblem(await post(`/incidents/${state.incidentId}/approve`, { candidate_id: candidate.id }));
    setSending(null);
  }

  const rows = [
    ["Regression check", "candidate"],
    ["Shop's own tests", "unit_tests"],
    ["Diff review", "diff_review_ok"],
  ] as const;
  const note = problem ?? state.releaseProblem;

  return (
    <Panel title="Independent verification · Verifier">
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_17.5rem] gap-4 px-4 pt-1 pb-3">
        {shown.length === 0 ? (
          <div className="-ml-4">
            {active ? <Waiting text="Waiting for a candidate fix…" /> : <p className="px-4 pt-1 text-dim">Tests every proposed fix</p>}
          </div>
        ) : (
          <div className="min-h-0 overflow-auto" role="region" aria-label="Verification results" tabIndex={0}>
            <table className="w-full text-left">
              <thead className="text-sm text-dim">
                <tr>
                  <th className="py-0.5 pr-3 font-medium">Check</th>
                  <th className="pr-3 font-medium">Broken</th>
                  <th className="pr-3 font-medium">Healthy</th>
                  {shown.map((item) => (
                    <th key={item.id} className="pr-3 font-medium whitespace-nowrap text-ink">{item.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map(([name, key]) => (
                  <tr key={key} className="border-t border-line">
                    <th className="py-0.5 pr-3 font-normal whitespace-nowrap">{name}</th>
                    <td className="pr-3">{key === "candidate" ? <Mark ok={first ? first.broken === "pass" : null} /> : "–"}</td>
                    <td className="pr-3">{key === "candidate" ? <Mark ok={first ? first.healthy === "pass" : null} /> : "–"}</td>
                    {shown.map((item) => {
                      const value = item.verification?.[key];
                      return (
                        <td key={item.id} className="pr-3 whitespace-nowrap">
                          <Mark ok={value === undefined ? null : value === true || value === "pass"} pending={value === undefined} />
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="flex min-h-0 flex-col justify-between gap-2">
          <div className="min-h-0 overflow-hidden">{latest ? <Stamp candidate={latest} /> : null}</div>
          <div>
            {note ? <p role="alert" className="mb-1 line-clamp-2 text-sm text-busy">⚠ {note}</p> : null}
            <button
              type="button"
              onClick={approve}
              disabled={!ready}
              className={`h-10 w-full rounded-md border text-base font-semibold ${
                ready ? "border-ink bg-ink text-ground" : "border-line text-dim"
              }`}
            >
              {releasing ? "Releasing…" : state.phase === "recovered" ? "✓ Released" : "Approve release"}
            </button>
          </div>
        </div>
      </div>
    </Panel>
  );
}
