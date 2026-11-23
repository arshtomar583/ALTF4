#!/usr/bin/env bash
# Break checkout, restart the brain mid-incident, and prove that it resumes. Uses the running stack.
set -uo pipefail
cd "$(dirname "$0")/.."
. scripts/common.sh

failures=0
result() { # result "name" true|false
  if [ "$2" = true ]; then
    printf '%-58s PASS\n' "$1"
  else
    printf '%-58s FAIL\n' "$1"
    failures=$((failures + 1))
  fi
}
count() { grep -c "$1" <<<"$log" || true; }
wait_for() { # wait_for pattern seconds
  local deadline=$((SECONDS + $2))
  while [ $SECONDS -lt $deadline ]; do
    log=$(events "$start")
    grep -qE "$1" <<<"$log" && return 0
    sleep 1
  done
  return 1
}

start=$(events "" | tail -n 1 | cut -d' ' -f1)
[ -n "$start" ] || { echo "The brain at $BRAIN is not answering"; exit 1; }
curl -sf -X POST "$BRAIN/chaos/break" -H 'Content-Type: application/json' -d '{"fault":"bad_commit"}' >/dev/null ||
  { echo "BREAK was refused. Is an incident already open?"; exit 1; }

if wait_for ' incident.restored ' 90; then restored=true; else restored=false; fi
# With one finding saved, the restart has real work to resume.
wait_for ' finding.added ' 60 || true
before=$(count ' finding.added ')
docker compose restart brain >/dev/null 2>&1
if wait_for ' system.recovered ' 60; then recovered=true; else recovered=false; fi
if wait_for ' (incident.escalated|verification.done) ' 240; then finished=true; else finished=false; fi
verified=false
deadline=$((SECONDS + 200))
while [ $finished = true ] && [ $SECONDS -lt $deadline ]; do
  if raw_events "$start" | grep '"type": "verification.done"' | grep -q '"verdict": "VERIFIED"'; then
    verified=true
    break
  fi
  events "$start" | grep -q ' incident.escalated ' && break
  sleep 2
done
log=$(events "$start")

result "Checkout was restored" $restored
result "A finding was saved before the restart" "$([ "$before" -ge 1 ] && echo true || echo false)"
result "system.recovered arrived after the restart" $recovered
result "Restore ran exactly once" "$([ "$(count ' incident.restored ')" = 1 ] && echo true || echo false)"
result "Only one incident was opened" "$([ "$(count ' incident.opened ')" = 1 ] && echo true || echo false)"
result "No release or rollback happened" "$([ "$(count ' release.done ')" = 0 ] && echo true || echo false)"
result "Code Investigator's finding was not repeated" "$([ "$(count ' finding.added code_investigator')" -le 1 ] && echo true || echo false)"
result "Runtime Investigator's finding was not repeated" "$([ "$(count ' finding.added runtime_investigator')" -le 1 ] && echo true || echo false)"
result "The fix reached VERIFIED" $verified
[ $failures -eq 0 ]
