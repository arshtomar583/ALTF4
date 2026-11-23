// Mirrors services/brain: event_store.after() for the envelope, agents/models.py for agent data.

export type Check = "pass" | "fail";

export type Evidence = {
  kind: "diff" | "log" | "config" | "request" | "source";
  ref: string | null;
  excerpt: string;
  url: string | null;
};

export type Finding = {
  agent: string;
  claim: string;
  confidence: number;
  suspect_type: "code" | "config" | "dependency" | "unknown";
  suspect_ref: string | null;
  evidence: Evidence[];
};

export type Decision = {
  cause_type: "code" | "config" | "unknown";
  suspect_ref: string | null;
  remedy: "code_fix" | "config_restore" | "escalate";
  reasoning: string;
  uses_findings: string[];
};

export type Verification = {
  candidate_id: string;
  rev: string;
  broken: Check;
  healthy: Check;
  candidate: Check;
  unit_tests: Check;
  diff_review_ok: boolean;
  issues: string[];
  verdict: "VERIFIED" | "REJECTED";
  reason: string;
};

export type Probe = {
  ok: boolean;
  status_code: number | null;
  latency_ms: number;
  slot: string | null;
  revision: string | null;
  discount: boolean;
  cart_total_cents: number | null;
};

type Payloads = {
  "checkout.probe": Probe;
  "chaos.break": object;
  "incident.opened": { broken_slot: string; broken_rev: string; healthy_rev: string; failing_request: unknown };
  "incident.restored": { active_slot: string; active_rev: string; seconds_to_restore: number };
  "system.recovered": { last_step: string | null };
  "agent.note": { usage?: unknown; error?: string };
  "agent.tool": { tool: string; args: object; ok: boolean; result_excerpt: string };
  "finding.added": Finding;
  "decision.made": Decision;
  "incident.escalated": { reason: string; evidence_pack_md: string };
  "candidate.proposed": { candidate_id: string; rev: string; diff: string; by: "agent" | "judge" };
  "verification.done": Verification;
  "approval.granted": { candidate_id: string; rev: string };
  "release.done": {
    candidate_id: string;
    ok: boolean;
    active_slot: string;
    active_rev: string;
    seconds_to_verified_fix: number | null;
    reason: string | null;
  };
  "report.ready": { markdown: string; pr_url: string | null };
};

export type PhoenixEvent = {
  [Type in keyof Payloads]: {
    id: number;
    type: Type;
    occurred_at: string;
    incident_id: string | null;
    actor: string;
    summary: string | null;
    data: Payloads[Type];
  };
}[keyof Payloads];
