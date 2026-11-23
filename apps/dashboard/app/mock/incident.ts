import type { PhoenixEvent } from "../lib/events";

const HEALTHY = "41a8d0da7d8de73aceb0f7dbf80c4fd328a43dd6";
const BROKEN = "b7c1e90f52d84a6c9e0d3a1f5b8274c6d9e01f23";
const FIXED = "2dd2d9ee21e407d7cd95fe7a8ab1e1a0ad0e7f97";
const FLAWED = "6539aaef1771cf5d41dab8eb428a1991ef43aa0a";
const ID = "inc_recorded";
const DIFF = `diff --git a/services/shoplite/src/shop.js b/services/shoplite/src/shop.js
@@ -27,1 +27,1 @@
-    const code = request.body.discount.code.trim().toUpperCase();
+    const code = request.body?.discount?.code;`;
const FLAWED_DIFF = `diff --git a/services/shoplite/src/shop.js b/services/shoplite/src/shop.js
@@ -41,1 +41,1 @@
-      totalCents: subtotalCents - discountCents,
+      totalCents: (() => { try { return subtotalCents - discountCents; } catch { return 0; } })(),`;
const REPORT = `# Incident report: ${ID}

This is the recorded story. A live incident fills this report from its own events.

| Measure | Value |
| --- | --- |
| Failed checkouts | 3 |

## Candidates

| Candidate | By | Verdict |
| --- | --- | --- |
| judge 1 | judge | REJECTED |
| c1 | agent | VERIFIED |

\`\`\`diff
${DIFF}
\`\`\`
`;

type Draft = { [Event in PhoenixEvent as Event["type"]]: [Event["type"], Event["data"]] }[PhoenixEvent["type"]];
type Step = { at: number; actor: string; summary: string; event: Draft };

const story: Step[] = [
  { at: 8, actor: "chaos", summary: "Checkout break requested", event: ["chaos.break", {}] },
  { at: 18.2, actor: "coordinator", summary: "Checkout incident opened on slot A", event: ["incident.opened", { broken_slot: "a", broken_rev: BROKEN, healthy_rev: HEALTHY, failing_request: {} }] },
  { at: 18.6, actor: "coordinator", summary: "Restored checkout on slot B", event: ["incident.restored", { active_slot: "b", active_rev: HEALTHY, seconds_to_restore: 0.4 }] },
  { at: 20, actor: "code_investigator", summary: "Bisecting 1 commit against the failing checkout…", event: ["agent.note", {}] },
  { at: 20.4, actor: "runtime_investigator", summary: "Reading what slot A printed as it failed…", event: ["agent.note", {}] },
  { at: 21.4, actor: "code_investigator", summary: "Runs git bisect", event: ["agent.tool", { tool: "git_bisect", args: {}, ok: true, result_excerpt: "" }] },
  { at: 21.8, actor: "runtime_investigator", summary: "Runs container logs", event: ["agent.tool", { tool: "container_logs", args: {}, ok: true, result_excerpt: "" }] },
  { at: 23, actor: "runtime_investigator", summary: "Looking up this exact error on the web…", event: ["agent.note", {}] },
  { at: 26, actor: "code_investigator", summary: "Every cart without a discount now crashes checkout.", event: ["finding.added", { agent: "code_investigator", claim: "Every cart without a discount now crashes checkout, because the code reads a discount that is not there.", confidence: 0.97, suspect_type: "code", suspect_ref: BROKEN, evidence: [{ kind: "diff", ref: "services/shoplite/src/shop.js:27", url: null, excerpt: "-    const code = request.body?.discount?.code;\n+    const code = request.body.discount.code.trim().toUpperCase();" }] }] },
  { at: 28, actor: "runtime_investigator", summary: "Checkout throws a TypeError. Configuration did not change.", event: ["finding.added", { agent: "runtime_investigator", claim: "Checkout throws a TypeError while reading the discount code. Configuration did not change.", confidence: 0.9, suspect_type: "code", suspect_ref: "src/shop.js:27", evidence: [{ kind: "log", ref: "lab replay", url: null, excerpt: "TypeError: Cannot read properties of undefined (reading 'code')" }, { kind: "source", ref: null, url: "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Errors/Unexpected_type", excerpt: "Reading a property of undefined throws a TypeError." }] }] },
  { at: 32, actor: "coordinator", summary: "Choosing a code fix: Both findings point at the same cause", event: ["decision.made", { cause_type: "code", suspect_ref: BROKEN, remedy: "code_fix", uses_findings: ["code_investigator", "runtime_investigator"], reasoning: "The code_investigator names the commit that reads a missing discount, and the runtime_investigator saw exactly that error in the logs. Configuration is unchanged, so this needs a code fix." }] },
  { at: 33, actor: "repair", summary: "Writing a minimal fix, attempt 1 of 3…", event: ["agent.note", {}] },
  { at: 36, actor: "repair", summary: "Runs run unit tests", event: ["agent.tool", { tool: "run_unit_tests", args: {}, ok: true, result_excerpt: "" }] },
  { at: 38, actor: "repair", summary: "Proposes fix 1 for verification", event: ["candidate.proposed", { candidate_id: `${ID}-c1`, rev: FIXED, diff: DIFF, by: "agent" }] },
  { at: 39, actor: "verifier", summary: "Testing the candidate with a check I write myself…", event: ["agent.note", {}] },
  { at: 44, actor: "verifier", summary: "Verified: The check fails on broken and passes on healthy and the candidate", event: ["verification.done", { candidate_id: `${ID}-c1`, rev: FIXED, broken: "fail", healthy: "pass", candidate: "pass", unit_tests: "pass", diff_review_ok: true, issues: [], verdict: "VERIFIED", reason: "The check fails on broken and passes on healthy and the candidate" }] },
  { at: 45.5, actor: "chaos", summary: "The judge submits a flawed fix for verification", event: ["candidate.proposed", { candidate_id: `${ID}-judge1`, rev: FLAWED, diff: FLAWED_DIFF, by: "judge" }] },
  { at: 52, actor: "verifier", summary: "Rejected: The candidate still fails the check; A catch block swallows the error", event: ["verification.done", { candidate_id: `${ID}-judge1`, rev: FLAWED, broken: "fail", healthy: "pass", candidate: "fail", unit_tests: "fail", diff_review_ok: false, issues: ["A catch block swallows the error"], verdict: "REJECTED", reason: "The candidate still fails the check; The candidate fails the shop's own tests; A catch block swallows the error" }] },
  { at: 54, actor: "release", summary: `Approved candidate ${ID}-c1`, event: ["approval.granted", { candidate_id: `${ID}-c1`, rev: FIXED }] },
  { at: 57, actor: "release", summary: "Verified checkout fix released", event: ["release.done", { candidate_id: `${ID}-c1`, ok: true, active_slot: "a", active_rev: FIXED, seconds_to_verified_fix: 38.8, reason: null }] },
  { at: 57.2, actor: "coordinator", summary: "Incident closed: checkout healthy on slot A @ 2dd2d9e", event: ["incident.closed", { status: "resolved", active_slot: "a", active_rev: FIXED }] },
  { at: 58, actor: "report", summary: "The incident report is ready", event: ["report.ready", { markdown: REPORT, pr_url: null }] },
];

/** Play the recorded story through the same events a live brain sends. Returns a stop function. */
export function play(emit: (event: PhoenixEvent) => void): () => void {
  let id = 0;
  const started = Date.now();
  const send = (actor: string, summary: string, [type, data]: Draft) =>
    emit({
      id: ++id,
      type,
      data,
      actor,
      summary,
      incident_id: ID,
      occurred_at: new Date().toISOString(),
    } as PhoenixEvent);

  const timers = story.map((step) =>
    window.setTimeout(() => send(step.actor, step.summary, step.event), step.at * 1000),
  );
  let count = 0;
  const probe = () => {
    const seconds = (Date.now() - started) / 1000;
    const discount = count++ % 2 === 1;
    const down = seconds > 8 && seconds < 18.6;
    const [slot, revision] = seconds < 18.6 ? ["a", down ? BROKEN : HEALTHY] : seconds < 57 ? ["b", HEALTHY] : ["a", FIXED];
    const ok = !down || discount;
    send("probe", ok ? "Checkout passed" : "Checkout failed", [
      "checkout.probe",
      { ok, status_code: ok ? 200 : 500, latency_ms: 6 + (count % 4), slot, revision, discount, cart_total_cents: discount ? 1080 : 1200 },
    ]);
  };
  probe();
  const ticker = window.setInterval(probe, 2000);
  return () => {
    window.clearInterval(ticker);
    timers.forEach((timer) => window.clearTimeout(timer));
  };
}
