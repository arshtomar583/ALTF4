import type { ReactNode } from "react";

import type { AgentId } from "../lib/incident-state";

type AgentView = { name: string; color: string; why: string };

export const AGENTS: Record<AgentId, AgentView> = {
  coordinator: { name: "Coordinator", color: "var(--agent-coordinator)", why: "no tools" },
  code_investigator: { name: "Code Investigator", color: "var(--agent-code)", why: "history, no logs" },
  runtime_investigator: { name: "Runtime Investigator", color: "var(--agent-runtime)", why: "logs, no history" },
  repair: { name: "Repair Agent", color: "var(--agent-repair)", why: "cannot judge itself" },
  verifier: { name: "Verifier", color: "var(--agent-verifier)", why: "never sees why" },
};

export function agent(actor: string): { name: string; color: string } {
  if (actor in AGENTS) return AGENTS[actor as AgentId];
  const words = actor.replaceAll("_", " ");
  return { name: words.charAt(0).toUpperCase() + words.slice(1), color: "var(--text-3)" };
}

export function Dot({ actor, className = "" }: { actor: string; className?: string }) {
  return <span aria-hidden className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${className}`} style={{ background: agent(actor).color }} />;
}

/** A panel on the stage. Director mode lights the one that just heard from its agent. */
export function Panel({ title, aside, children, spot, lit = false, dim = false, className = "" }: {
  title: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  spot?: string;
  lit?: boolean;
  dim?: boolean;
  className?: string;
}) {
  return (
    <section
      data-dim={dim}
      className={`stage relative flex min-h-0 min-w-0 flex-col overflow-hidden rounded-panel border border-line bg-panel ${className}`}
    >
      <span
        aria-hidden
        className="mood absolute inset-x-0 top-0 h-px"
        data-on={lit}
        style={{ background: spot ? agent(spot).color : undefined }}
      />
      <div className="flex shrink-0 items-center gap-3 px-4 pt-2">
        <h2 className="min-w-0 flex-1 truncate text-base font-semibold 2xl:text-title">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

export function Scroll({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div role="region" aria-label={label} tabIndex={0} className="min-h-0 flex-1 overflow-y-auto px-4 pt-1 pb-3">
      {children}
    </div>
  );
}

export function Code({ text, diff = false }: { text: string; diff?: boolean }) {
  return (
    <pre className="my-1 overflow-hidden rounded-control border border-line bg-bg py-1 font-mono text-meta leading-normal whitespace-pre-wrap wrap-anywhere">
      {text.split("\n").map((line, index) => {
        const sign = diff && /^[+-]/.test(line) ? line[0] : "";
        const tint = sign === "+" ? "diff-add" : sign === "-" ? "diff-remove" : "";
        return (
          <span key={index} className={`flex gap-2 px-2.5 ${tint}`}>
            {diff ? <span className="w-[1ch] shrink-0 text-text-2">{sign === "-" ? "−" : sign}</span> : null}
            <span className="min-w-0 flex-1">{(sign ? line.slice(1) : line) || " "}</span>
          </span>
        );
      })}
    </pre>
  );
}

/** A still placeholder with the shape of what will arrive. */
export function Skeleton({ text, lines = 2 }: { text: string; lines?: number }) {
  return (
    <div className="px-4 pt-1">
      <p className="text-text-2">{text}</p>
      {Array.from({ length: lines }, (_, index) => (
        <div key={index} aria-hidden className="mt-2 h-3 rounded-sm bg-panel-2" style={{ width: `${88 - index * 22}%` }} />
      ))}
    </div>
  );
}
