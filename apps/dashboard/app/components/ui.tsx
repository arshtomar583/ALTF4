import type { ReactNode } from "react";

export const AGENTS: Record<string, { name: string; color: string }> = {
  coordinator: { name: "Coordinator", color: "#FF5A1F" },
  code_investigator: { name: "Code Investigator", color: "#3E63DD" },
  runtime_investigator: { name: "Runtime Investigator", color: "#5B7BEA" },
  repair: { name: "Repair Agent", color: "#12A594" },
  verifier: { name: "Verifier", color: "#7C5CFF" },
};

export function agent(actor: string) {
  return AGENTS[actor] ?? { name: actor.charAt(0).toUpperCase() + actor.slice(1), color: "#98A2B3" };
}

export function Dot({ actor }: { actor: string }) {
  return <span aria-hidden className="mt-2 h-2 w-2 shrink-0 rounded-full" style={{ background: agent(actor).color }} />;
}

export function Panel({ title, children, className = "" }: { title: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`flex min-h-0 min-w-0 flex-col rounded-lg border border-line bg-panel ${className}`}>
      <h2 className="shrink-0 px-4 pt-2.5 text-sm font-medium text-dim">{title}</h2>
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
    <pre className="my-1 overflow-x-auto rounded border border-line bg-ground px-2.5 py-1 font-mono text-sm leading-snug whitespace-pre-wrap wrap-anywhere">
      {text.split("\n").map((line, index) => {
        const tone = !diff ? "" : line.startsWith("+") ? "text-good" : line.startsWith("-") ? "text-bad" : "text-dim";
        return (
          <span key={index} className={`block ${tone}`}>
            {line || " "}
          </span>
        );
      })}
    </pre>
  );
}

export function Waiting({ text }: { text: string }) {
  return <p className="waiting px-4 pt-1 text-dim">{text}</p>;
}
