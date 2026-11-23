import { memo } from "react";

import type { Candidate } from "../lib/incident-state";
import type { Controls } from "../lib/use-controls";
import { Check, Cross, Warn } from "./icons";
import { Dot, Panel, Skeleton } from "./ui";

function Mark({ ok, pending = false }: { ok: boolean | null; pending?: boolean }) {
  if (ok === null) return <span className="text-text-3">{pending ? "…" : "—"}</span>;
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      {ok ? <Check className="h-4 w-4 text-ok" /> : <Cross className="h-4 w-4 text-down" />}
      <span className="text-meta">{ok ? "pass" : "fail"}</span>
    </span>
  );
}

function Name({ candidate }: { candidate: Candidate }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      <span className="tnum text-text">{candidate.label.replace("judge ", "j")}</span>
      {candidate.by === "judge" ? <span className="rounded-sm border border-text-3 px-1 text-meta leading-tight text-text-2">JUDGE</span> : null}
    </span>
  );
}

function Stamp({ candidate }: { candidate: Candidate }) {
  const result = candidate.verification;
  if (!result) {
    return (
      <div className="min-w-0">
        <p className="text-text-2">Verifying <Name candidate={candidate} />…</p>
      </div>
    );
  }
  const ok = result.verdict === "VERIFIED";
  const Icon = ok ? Check : Cross;
  return (
    <div className="min-w-0">
      <p
        className={`stamp mt-2 mb-2.5 inline-flex origin-left items-center gap-1.5 rounded-sm border-2 px-2 text-state leading-tight font-semibold ${
          ok ? "border-ok text-ok" : "border-down text-down"
        }`}
      >
        <Icon className="h-5 w-5" /> {result.verdict}
      </p>
      <p className="line-clamp-2 text-meta leading-snug text-text-2" title={result.reason}>
        <Name candidate={candidate} /> {result.reason}
      </p>
    </div>
  );
}

const ROWS = [
  ["Regression check", "candidate"],
  ["Unit tests", "unit_tests"],
  ["Diff review", "diff_review_ok"],
] as const;

export const Verification = memo(function Verification({ candidates, active, spot, lit, dim, controls }: {
  candidates: Candidate[];
  active: boolean;
  spot: string;
  lit: boolean;
  dim: boolean;
  controls: Controls;
}) {
  // The newest candidates matter most, and four columns fit the projector.
  const shown = candidates.slice(-4);
  const answered = shown.filter((item) => item.verification);
  const waiting = shown.findLast((item) => !item.verification);
  const stamps = [...answered, ...(waiting ? [waiting] : [])].slice(-2);
  const first = answered[0]?.verification ?? null;
  const { approve } = controls;
  return (
    <Panel
      title={<span className="inline-flex items-center gap-2"><Dot actor="verifier" /> Independent verification</span>}
      spot={spot}
      lit={lit}
      dim={dim}
    >
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_24.5rem_8.5rem] gap-3 px-4 pt-1 pb-3">
        {shown.length === 0 ? (
          <div className="-ml-4"><Skeleton text={active ? "Waiting for a candidate fix…" : "Tests every proposed fix"} /></div>
        ) : (
          <div className="min-h-0 overflow-auto" role="region" aria-label="Verification results" tabIndex={0}>
            <table className="w-full text-left leading-tight">
              <thead>
                <tr>
                  <th className="pr-2 pb-1"><span className="label">Check</span></th>
                  <th className="pr-2 pb-1"><span className="label">Broken</span></th>
                  <th className="pr-2 pb-1"><span className="label">Healthy</span></th>
                  {shown.map((item) => <th key={item.id} className="pr-2 pb-1 font-medium"><Name candidate={item} /></th>)}
                </tr>
              </thead>
              <tbody>
                {ROWS.map(([name, key]) => (
                  <tr key={key} className="border-t border-line">
                    <th className="py-1.5 pr-2 text-meta font-normal whitespace-nowrap">{name}</th>
                    <td className="pr-2 align-middle">{key === "candidate" ? <Mark ok={first ? first.broken === "pass" : null} /> : <Mark ok={null} />}</td>
                    <td className="pr-2 align-middle">{key === "candidate" ? <Mark ok={first ? first.healthy === "pass" : null} /> : <Mark ok={null} />}</td>
                    {shown.map((item) => {
                      const value = item.verification?.[key];
                      return (
                        <td key={item.id} className="pr-2">
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
        <div className="grid min-h-0 grid-cols-2 gap-4">
          {stamps.map((item) => <Stamp key={item.id} candidate={item} />)}
        </div>
        <div className="flex min-h-0 flex-col justify-end gap-1.5">
          {approve.problem ? <p role="alert" className="line-clamp-3 text-meta leading-snug text-text"><Warn /> {approve.problem}</p> : null}
          <button
            type="button"
            onClick={approve.run}
            disabled={!approve.enabled}
            className={`control relative min-h-12 w-full px-1 text-meta 2xl:text-base overflow-hidden rounded-control border font-semibold ${
              approve.enabled ? "border-ok bg-ok text-bg" : "border-line text-text-3"
            }`}
          >
            {approve.label}
            {approve.busy ? <span aria-hidden className="progress absolute bottom-0 left-0 h-0.5 w-1/3 bg-ok" /> : null}
          </button>
        </div>
      </div>
    </Panel>
  );
});
