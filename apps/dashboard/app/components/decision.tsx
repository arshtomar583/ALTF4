import type { Decision as DecisionData } from "../lib/events";
import { Panel, Scroll, Waiting } from "./ui";

const REMEDY = { code_fix: "Code fix", config_restore: "Restore configuration", escalate: "Ask a human" };

export function Decision({ decision, escalation, active }: {
  decision: DecisionData | null;
  escalation: string | null;
  active: boolean;
}) {
  if (escalation) {
    return (
      <Panel title="Escalated to a human" className="border-busy">
        <Scroll label="Escalation">
          <p>
            <strong className="text-busy">⚠ Escalated.</strong> {escalation}
          </p>
        </Scroll>
      </Panel>
    );
  }
  return (
    <Panel title="Decision · Coordinator">
      {decision ? (
        <Scroll label="Decision">
          <p className="leading-snug">
            <strong>
              Cause: {decision.cause_type}. Remedy: {REMEDY[decision.remedy].toLowerCase()}.
            </strong>{" "}
            {decision.reasoning}
          </p>
        </Scroll>
      ) : active ? (
        <Waiting text="Waiting for both findings…" />
      ) : (
        <p className="px-4 pt-1 text-dim">Follows the two findings</p>
      )}
    </Panel>
  );
}
