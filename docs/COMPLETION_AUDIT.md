# PHOENIX completion audit

Checked on 23 November 2026 at about 15:00 IST, against `origin/main` at `8b8ce6b` and `nick-polish` at `f3e4892`.

- ✅ works, with evidence from code and a test or a live run
- ⚠️ works with a limit, or not fully proven
- ❌ missing or failing

"Live run" means a full incident in Docker on a separate Compose project with Gemini and the real fault commit. Test command: `cd services && python -m pytest tests brain/tests version_switch/tests`, which gave **186 passed**.

## Engine (Arsh)

| Item | Status | Evidence |
| --- | --- | --- |
| Event contract, incidents and steps tables | ✅ | `services/brain/event_store.py`, `brain/tests/test_event_store.py` |
| Switch persists its slot | ✅ | `version_switch/api.py` `save_state`, `version_switch/tests/test_switch_state.py` |
| Slots run Git revisions | ✅ | `version_switch/revisions.py` `checkout_rev`; live run deployed the fault and the fix |
| Three failures open an incident | ✅ | `brain/incident.py` `ProbeFailures`, `brain/tests/test_incident.py` |
| Restore with a re-probe | ✅ | `brain/incident.py` `restore`; live runs restored 0.3 s to 0.9 s after opening |
| BREAK endpoint | ✅ | `brain/api.py` `/chaos/break`; live run |
| Approval only for VERIFIED | ✅ | `event_store.py` `_verified_revision`; approving the judge's candidate returned 409 |
| Release runner with rollback | ⚠️ | `brain/release.py`, `brain/tests/test_release.py`. Release ran live; rollback is covered by tests only |
| Restart safety (`system.recovered`) | ✅ | `agents/pipeline.py` `resume`; `scripts/restart_check.sh` passed 9 of 9, before the last small repair-agent change |
| Incident closes after a release | ✅ | `event_store.py` `_close`; two incidents ran back to back without a reset |

## AI layer (Nick)

| Item | Status | Evidence |
| --- | --- | --- |
| Agent loop and per-agent tool allow-lists | ✅ | `brain/llm.py`, `tests/test_agents_llm.py`, `tests/test_agents_diagnose.py` |
| Lab on port 3003 | ✅ | `brain/lab.py`, `tests/test_agents_lab.py` |
| Code Investigator with bisect | ✅ | Live runs named `0b0673f` |
| Runtime Investigator, logs and cited source | ⚠️ | Quotes the real log line. No Tavily key is set, so search uses keyless mode, which often answers 429. Confidence is then capped at 60% and no source is cited |
| Coordinator decision citing A and B | ✅ | `agents/coordinator.py` rejects reasoning that does not name both |
| Repair, at most three tries | ✅ | `agents/pipeline.py` `MAX_ATTEMPTS`, `tests/test_agents_repair.py` |
| Independent verifier, gate in code | ✅ | `agents/verifier.py` `enforce`; it receives a revision, never the rationale |
| Bad-candidate endpoint | ✅ | Live runs ended REJECTED. On this fault the candidate still crashes, because the fault throws before the line it wraps |
| Report and `report.ready` | ⚠️ | `brain/report.py`; live run. The GitHub pull request is tested only against a fake |
| Rate-limit handling | ⚠️ | `brain/llm.py` `Limiter`; live run. The Claude backup is tested only with a fake |

Time to a verified fix in the last three live runs: 76.2 s, 70.1 s and 70.5 s. The cap of 12 model calls a minute dominates.

## Shop and fault (originally Likhita)

| Item | Status | Evidence |
| --- | --- | --- |
| SAVE10 discounts and tests | ✅ | `services/shoplite`, 8 of 8 tests pass |
| Probe alternates carts | ✅ | `brain/probe.py`, `tests/test_probe.py` |
| `fault/bad-checkout` exists and is not on main | ✅ | `0b0673f7378bb3bad237329bf46e6b4c3ab22b62`, not an ancestor of `main` or `nick-polish` |
| Fault SHA documented | ✅ | Here. The README shows the command that prints it |
| `scripts/reset.sh` | ⚠️ | Ran twice from a clean clone, with changed ports. The default ports were in use on that machine |
| README demo and Linux commands | ✅ | `README.md`, Before the demo and Demo order |
| Clean-clone test | ⚠️ | Passed for the demo setup before the last README edits. The Windows, PowerShell and non-Docker steps are untested |

## Dashboard (originally Srushti)

Rebuilt in `fa53afc` (on `main`) and `f3e4892` (on `nick-polish`). QA ran at 1280×720 and 1920×1080 with simulated data, and one full live incident ran at 1280×720 on `fa53afc`.

| Item | Status | Evidence |
| --- | --- | --- |
| 1 State pill and strip mood | ✅ | Live run; three pulses, then solid |
| 2 Heartbeat, freshness, stale, pause | ✅ | STALE and pause checked in a browser |
| 3 Evidence wall, thread, decision | ⚠️ | At 1280×720 the evidence under a long claim needs scrolling inside its card |
| 4 Verification and stamps | ✅ | REJECTED and VERIFIED together, live |
| 5 Control rail and gating | ✅ | Judge action and approval gated, live. Approve sits beside the stamps, as in the layout sketch |
| 6 Director mode | ⚠️ | Dimming and lighting checked live. The `D` toggle has not been exercised |
| Feed, report drawer, recovery banner, escalated state | ✅ | Recovery and escalation checked with injected test events |
| Shortcuts and announcements | ⚠️ | `B`, `A`, `R` and `?` used in a browser; `D` not exercised |
| Simulated and replay banners | ⚠️ | Simulated checked on the new layout. Replay checked before the rebuild only |
| Reduced motion | ✅ | No running animations under `prefers-reduced-motion` |
| Contrast | ✅ | `--text-3` was raised to `#7a8496` (4.98:1 on panels), because `#6b7586` measured 4.04:1. Director mode dims inactive panels below that on purpose |
| Layout shift | ✅ | 0.015 in a whole live incident, from new feed rows. Simulated and replay modes add one shift when the banner appears |
| Projector QA screenshots | ⚠️ | Taken at both sizes and reviewed, but not stored in the repository |

## Submission

| Item | Status | Evidence |
| --- | --- | --- |
| Public repository named after the team | ✅ | `arshtomar583/ALTF4`, public |
| `.env.example` complete | ⚠️ | Missing only the optional `PHOENIX_DASHBOARD_ORIGINS`, which Compose derives |
| No secrets in history | ✅ | A pattern search over `git log --all -p` found none, and `.env` was never committed |
| README quickstart | ✅ | `README.md` |
| Fault branch never merged | ✅ | `git merge-base --is-ancestor` fails for `main` |
| No force pushes from here | ⚠️ | `main` was force-updated once earlier (`3ee5477` to `2b9ce13` in the fetch log). Branch protection is off, so this depends on the team |

## Remaining before 15:30

| Owner | Action | Minutes |
| --- | --- | --- |
| Nick | Open and merge the pull request from `nick-polish` to `main` (QA fixes and this audit) | 3 |
| Arsh | Review the closing code in `event_store.py` | 5 |
| Demo laptop | `git switch main && git pull --ff-only && git fetch origin fault/bad-checkout` | 2 |
| Demo laptop | Fill `.env`: model key, `TAVILY_API_KEY`, `PHOENIX_BAD_REV` | 3 |
| Demo laptop | `docker compose build && ./scripts/reset.sh && ./scripts/preflight.sh` until GO | 8 |
| Team | One full rehearsal, then stop, to save the daily model quota | 5 |
| Everyone | No force pushes, and never merge `fault/bad-checkout` | 0 |
