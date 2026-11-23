import { memo } from "react";

import { AGENT_IDS, type AgentStatus, type Roster } from "../lib/incident-state";
import type { Controls } from "../lib/use-controls";
import { Bolt, Check, Cross, Warn } from "./icons";
import { AGENTS } from "./ui";

function Status({ status, color }: { status: AgentStatus; color: string }) {
  if (status === "done") return <Check className="h-4 w-4 text-text" />;
  if (status === "failed") return <Cross className="h-4 w-4 text-text" />;
  return (
    <span
      aria-hidden
      className={`block h-2.5 w-2.5 rounded-full border-2 ${status === "working" ? "working" : ""}`}
      style={{ borderColor: color, background: status === "working" ? color : undefined }}
    />
  );
}

const Team = memo(function Team({ agents }: { agents: Roster }) {
  const busy = AGENT_IDS.find((id) => agents[id] === "working");
  return (
    <div className="mt-auto border-t border-line pt-3">
      <h2 className="label mb-1.5">Agents</h2>
      <ul className="grid gap-1">
        {AGENT_IDS.map((id) => (
          <li key={id} title={AGENTS[id].why} className={`flex items-center gap-2.5 text-meta ${agents[id] === "working" ? "text-text" : "text-text-2"}`}>
            <span className="grid w-4 shrink-0 place-items-center"><Status status={agents[id]} color={AGENTS[id].color} /></span>
            <span className="truncate">{AGENTS[id].name}</span>
            <span className="sr-only">: {agents[id]}. {AGENTS[id].why}</span>
          </li>
        ))}
      </ul>
      <p aria-hidden className="mt-1.5 min-h-9 text-meta leading-snug text-text-2">{busy ? `Now: ${AGENTS[busy].why}` : ""}</p>
    </div>
  );
});

function Problem({ text }: { text: string | null }) {
  return text ? <p role="alert" className="text-meta leading-snug wrap-anywhere text-text"><Warn /> {text}</p> : null;
}

export function Rail({ controls, agents, onHelp }: { controls: Controls; agents: Roster; onHelp: () => void }) {
  const { breakIt, judge } = controls;
  return (
    <aside aria-label="Judge controls and agents" className="flex min-h-0 flex-col gap-2.5 overflow-y-auto rounded-panel border border-line bg-panel px-4 pt-4 pb-1">
      <div className="flex flex-col items-center gap-1.5">
        <button
          type="button"
          aria-label="Break checkout"
          disabled={!breakIt.enabled}
          onClick={breakIt.run}
          className={`control grid h-28 w-28 shrink-0 place-content-center justify-items-center rounded-full border-2 text-base font-semibold tracking-wide ${
            breakIt.enabled ? "border-down text-text" : "border-line text-text-3"
          }`}
        >
          <Bolt className="h-6 w-6" />
          {breakIt.label}
        </button>
        <p className="min-h-5 text-center text-meta leading-snug text-text-2">{breakIt.hint}</p>
        <Problem text={breakIt.problem} />
      </div>
      <div className="flex flex-col gap-1.5">
        <button
          type="button"
          disabled={!judge.enabled}
          onClick={judge.run}
          className={`control min-h-11 rounded-control border px-2 text-meta leading-snug ${judge.enabled ? "border-text-2 text-text" : "border-line text-text-3"}`}
        >
          {judge.label}
        </button>
        <p className="min-h-5 text-center text-meta leading-snug text-text-2">{judge.hint}</p>
        <Problem text={judge.problem} />
      </div>
      <Team agents={agents} />
      <button type="button" onClick={onHelp} className="control min-h-11 shrink-0 text-meta text-text-2">
        <kbd className="tnum">?</kbd> Shortcuts
      </button>
    </aside>
  );
}
