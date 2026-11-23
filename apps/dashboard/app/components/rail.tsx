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
  return (
    <div className="border-t border-line pt-1.5 sm:col-span-2 xl:mt-auto">
      <h2 className="label">Agents</h2>
      <ul className="grid gap-y-1 sm:grid-cols-2 xl:block">
        {AGENT_IDS.map((id) => (
          <li key={id} className="grid grid-cols-[1rem_minmax(0,1fr)] items-center gap-x-2.5 border-l-2 pl-2" style={{ borderColor: AGENTS[id].color }}>
            <span className="grid place-items-center"><Status status={agents[id]} color={AGENTS[id].color} /></span>
            <span className="truncate text-meta leading-tight font-semibold">{AGENTS[id].name}</span>
            <span className="col-start-2 text-meta leading-tight text-text-2">
              <span className="sr-only">{agents[id]}. </span>
              {AGENTS[id].why}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
});

function Problem({ text }: { text: string | null }) {
  return text ? <p role="alert" className="text-meta leading-snug wrap-anywhere text-text"><Warn /> {text}</p> : null;
}

export function Rail({ controls, agents, onHelp, onBreak }: { controls: Controls; agents: Roster; onHelp: () => void; onBreak: () => void }) {
  const { breakIt, judge } = controls;
  return (
    <aside aria-label="Judge controls and agents" className="grid min-h-0 gap-3 sm:grid-cols-2 xl:flex xl:flex-col xl:gap-1.5 xl:overflow-y-auto rounded-panel border border-line bg-panel px-3 pt-3 pb-1">
      <div className="flex flex-col items-center gap-1.5">
        <button
          type="button"
          aria-label="Break checkout"
          disabled={!breakIt.enabled}
          onClick={onBreak}
          className={`control grid h-28 w-28 shrink-0 place-content-center justify-items-center rounded-full border-2 text-title font-semibold ${
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
