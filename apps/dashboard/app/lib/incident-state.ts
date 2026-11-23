import type { Decision, Finding, PhoenixEvent, Probe, Verification } from "./events";

export type Phase =
  | "healthy"
  | "down"
  | "fixing"
  | "verified"
  | "releasing"
  | "recovered"
  | "escalated";

export type Candidate = {
  id: string;
  label: string;
  by: "agent" | "judge";
  rev: string;
  diff: string;
  verification: Verification | null;
};

export type Beat = Probe & { id: number; outage: boolean };
export type Marker = { id: number; label: string };
export type FeedItem = PhoenixEvent;

export type IncidentState = {
  phase: Phase;
  incidentId: string | null;
  beats: Beat[];
  markers: Marker[];
  feed: FeedItem[];
  slot: string | null;
  revision: string | null;
  breakId: number | null;
  openedAt: number | null;
  restoredAt: number | null;
  verifiedAt: number | null;
  failedCheckouts: number;
  findings: { code: Finding | null; runtime: Finding | null };
  decision: Decision | null;
  escalation: string | null;
  candidates: Candidate[];
  releaseProblem: string | null;
  report: { markdown: string; prUrl: string | null } | null;
  closed: boolean;
  recovered: { id: number; at: number; step: string | null } | null;
};

export const BEATS = 60;
const FEED_LIMIT = 200;

export const initialState: IncidentState = {
  phase: "healthy",
  incidentId: null,
  beats: [],
  markers: [],
  feed: [],
  slot: null,
  revision: null,
  breakId: null,
  openedAt: null,
  restoredAt: null,
  verifiedAt: null,
  failedCheckouts: 0,
  findings: { code: null, runtime: null },
  decision: null,
  escalation: null,
  candidates: [],
  releaseProblem: null,
  report: null,
  closed: false,
  recovered: null,
};

export type Action = { type: "events"; events: PhoenixEvent[] } | { type: "reset" };

export function reducer(state: IncidentState, action: Action): IncidentState {
  return action.type === "reset" ? initialState : action.events.reduce(apply, state);
}

/** Everything that belongs to one incident starts over; the heartbeat and the feed carry on. */
function fresh(state: IncidentState): IncidentState {
  const { beats, markers, feed, slot, revision } = state;
  return { ...initialState, beats, markers, feed, slot, revision };
}

function mark(state: IncidentState, id: number, label: string): IncidentState {
  return { ...state, markers: [...state.markers, { id, label }].slice(-12) };
}

function label(id: string, by: "agent" | "judge", candidates: Candidate[]): string {
  const same = candidates.filter((candidate) => candidate.by === by).length + 1;
  if (by === "judge") return `judge ${same}`;
  return id.match(/-(c\d+)$/)?.[1] ?? `c${same}`;
}

function apply(previous: IncidentState, event: PhoenixEvent): IncidentState {
  const at = Date.parse(event.occurred_at);
  if (event.type === "checkout.probe") return probe(previous, event.id, event.data);

  const state: IncidentState = { ...previous, feed: [event, ...previous.feed].slice(0, FEED_LIMIT) };
  switch (event.type) {
    case "chaos.break":
      return mark({ ...fresh(state), breakId: event.id }, event.id, "BREAK");
    case "incident.opened": {
      // A break belongs to the incident it caused, so its failed checkouts are kept.
      const base = state.incidentId === null && state.breakId !== null ? state : fresh(state);
      return mark(
        { ...base, phase: "down", incidentId: event.incident_id, openedAt: at, breakId: base.breakId ?? event.id },
        event.id,
        "opened",
      );
    }
    case "incident.restored":
      return mark({ ...state, phase: "fixing", restoredAt: at }, event.id, "restored");
    case "system.recovered":
      return { ...state, recovered: { id: event.id, at, step: event.data.last_step } };
    case "finding.added": {
      const key = event.data.agent === "code_investigator" ? "code" : "runtime";
      return { ...state, findings: { ...state.findings, [key]: event.data } };
    }
    case "decision.made":
      return { ...state, decision: event.data };
    case "incident.escalated":
      return { ...state, escalation: event.data.reason, phase: state.verifiedAt ? state.phase : "escalated" };
    case "candidate.proposed": {
      const { candidate_id: id, by, rev, diff } = event.data;
      if (state.candidates.some((candidate) => candidate.id === id)) return state;
      const added = { id, by, rev, diff, label: label(id, by, state.candidates), verification: null };
      return { ...state, candidates: [...state.candidates, added] };
    }
    case "verification.done": {
      const candidates = state.candidates.map((candidate) =>
        candidate.id === event.data.candidate_id ? { ...candidate, verification: event.data } : candidate,
      );
      if (event.data.verdict !== "VERIFIED") return { ...state, candidates };
      return {
        ...state,
        candidates,
        verifiedAt: state.verifiedAt ?? at,
        phase: state.phase === "fixing" || state.phase === "escalated" ? "verified" : state.phase,
      };
    }
    case "approval.granted":
      return { ...state, phase: "releasing", releaseProblem: null };
    case "release.done":
      return event.data.ok
        ? mark({ ...state, phase: "recovered" }, event.id, "released")
        : { ...state, phase: "verified", releaseProblem: event.data.reason ?? "The release stopped" };
    case "incident.closed":
      return { ...state, closed: true, phase: event.data.status === "resolved" ? "recovered" : "escalated" };
    case "report.ready":
      return { ...state, report: { markdown: event.data.markdown, prUrl: event.data.pr_url } };
    default:
      return state;
  }
}

function probe(state: IncidentState, id: number, data: Probe): IncidentState {
  const counting = state.breakId !== null && state.restoredAt === null;
  const down = !data.ok && counting;
  const phase = down && state.phase === "healthy" ? "down" : state.phase;
  const beats = [...state.beats, { ...data, id, outage: phase === "down" }].slice(-BEATS);
  return {
    ...state,
    phase,
    beats,
    markers: state.markers.filter((marker) => marker.id >= beats[0].id),
    slot: data.slot ?? state.slot,
    revision: data.revision ?? state.revision,
    failedCheckouts: state.failedCheckouts + (down ? 1 : 0),
  };
}

/** A judge's candidate the verifier has not answered yet. */
export function pendingJudge(state: IncidentState): Candidate | null {
  return state.candidates.find(({ by, verification }) => by === "judge" && !verification) ?? null;
}

export function approvable(state: IncidentState): Candidate | null {
  return state.candidates.findLast(({ by, verification }) => by === "agent" && verification?.verdict === "VERIFIED") ?? null;
}
