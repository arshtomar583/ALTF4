import { Fragment, memo } from "react";

import type { Decision as DecisionData } from "../lib/events";
import { Warn } from "./icons";
import { Dot, Panel, Scroll, Skeleton } from "./ui";

const REMEDY = { code_fix: "Code fix", config_restore: "Restore configuration", escalate: "Ask a human" };
const CITATION = /(code[ _]investigator|runtime[ _]investigator)/gi;

/** Show where the reasoning leans on each finding. */
function Reasoning({ text }: { text: string }) {
  return text.split(CITATION).map((part, index) => {
    if (index % 2 === 0) return <Fragment key={index}>{part}</Fragment>;
    const code = /^code/i.test(part);
    return (
      <strong key={index} className="rounded-sm bg-panel-2 px-1.5 font-medium whitespace-nowrap">
        <Dot actor={code ? "code_investigator" : "runtime_investigator"} className="mr-1 align-baseline" />
        {code ? "A · Code" : "B · Runtime"}
      </strong>
    );
  });
}

export const Decision = memo(function Decision({ decision, escalation, active, onPack }: {
  decision: DecisionData | null;
  escalation: { reason: string; pack: string } | null;
  active: boolean;
  onPack: () => void;
}) {
  const title = (
    <span className="inline-flex items-center gap-2">
      <Dot actor="coordinator" /> {escalation ? "Escalated to a human" : "Decision"}
    </span>
  );
  if (escalation) {
    const button = escalation.pack ? (
      <button type="button" onClick={onPack} className="control h-11 shrink-0 rounded-control border border-line px-3 text-meta text-text">
        Open the evidence pack
      </button>
    ) : null;
    return (
      <Panel title={title} aside={button}>
        <Scroll label="Escalation">
          <p className="leading-snug"><Warn /> {escalation.reason}. Checkout stays on the healthy version.</p>
        </Scroll>
      </Panel>
    );
  }
  return (
    <Panel title={title}>
      {decision ? (
        <Scroll label="Decision">
          <div className="appear grid gap-3 sm:grid-cols-[auto_minmax(0,1fr)] sm:gap-x-6">
            <dl className="grid grid-cols-[auto_auto] gap-x-3 self-start whitespace-nowrap">
              <dt className="label self-center">Cause</dt>
              <dd className="font-semibold first-letter:uppercase">{decision.cause_type}</dd>
              <dt className="label self-center">Remedy</dt>
              <dd className="font-semibold">{REMEDY[decision.remedy]}</dd>
            </dl>
            <p className="leading-snug"><Reasoning text={decision.reasoning} /></p>
          </div>
        </Scroll>
      ) : (
        <Skeleton text={active ? "Waiting for both findings…" : "Follows the two findings"} lines={1} />
      )}
    </Panel>
  );
});
