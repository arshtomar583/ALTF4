#!/usr/bin/env bash
# Save one real incident's events for the dashboard's ?replay=recorded. Usage: record_run.sh <incident-id>
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/common.sh

incident=${1:?Usage: scripts/record_run.sh <incident-id>}
target=apps/dashboard/app/mock/recorded.json
pending=$(mktemp)
trap 'rm -f "$pending"' EXIT

docker compose exec -T brain python - "$incident" >"$pending" <<'PY'
import json
import os
import sys
from datetime import datetime, timedelta

from brain.agents import bridge

incident = sys.argv[1]
events = bridge.events()
own = [event for event in events if event["incident_id"] == incident]
if not own:
    sys.exit(f"No events are recorded for {incident}")


def when(event):
    return datetime.fromisoformat(event["occurred_at"])


opened = next(event for event in own if event["type"] == "incident.opened")
breaks = [e for e in events if e["type"] == "chaos.break" and e["id"] < opened["id"]]
first = breaks[-1] if breaks else opened
start, end = when(first) - timedelta(seconds=8), when(own[-1]) + timedelta(seconds=6)
kept = [
    event
    for event in events
    if start <= when(event) <= end and event["incident_id"] in (None, incident)
]
recording = {"recorded_at": kept[0]["occurred_at"], "incident_id": incident, "events": kept}
text = json.dumps(recording, indent=1)
secrets = [os.getenv(name) for name in os.environ if name.endswith(("_KEY", "_TOKEN"))]
if any(secret and secret in text for secret in secrets):
    sys.exit("The recording contains a secret, so it was not saved")
print(text)
PY
if grep -qE 'AIza[A-Za-z0-9_-]{20}|AQ\.[A-Za-z0-9_-]{15}|sk-ant-[a-z0-9]|tvly-[A-Za-z0-9]{8}|gh[po]_[A-Za-z0-9]{10}' "$pending"; then
  echo "The recording looks like it contains a key, so it was not saved" >&2
  exit 1
fi
cat "$pending" >"$target"
echo "Recorded $(grep -c '"id":' "$target") events to $target"
