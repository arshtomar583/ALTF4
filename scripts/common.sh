# Shared by the demo scripts. Source it from the repository root.

# An exported variable wins over .env, as it does for docker compose.
setting() {
  local value=${!1:-}
  if [ -z "$value" ] && [ -f .env ]; then
    value=$(sed -n "s/^$1=//p" .env | tail -n 1 | tr -d "\"'\r" | sed 's/[[:space:]]*$//')
  fi
  echo "${value:-${2:-}}"
}

BRAIN="127.0.0.1:$(setting PHOENIX_BRAIN_PORT 8000)"
SWITCH="127.0.0.1:$(setting PHOENIX_SWITCH_PORT 8080)"

# Raw event JSON after an event ID. The stream never ends, so curl stops on its own.
raw_events() {
  curl -sN -m 2 ${1:+-H "Last-Event-ID: $1"} "$BRAIN/events" 2>/dev/null | sed -n 's/^data: //p' || true
}

# One "id type actor" line per event after an event ID.
events() {
  raw_events "$1" |
    sed -nE 's/^\{"id": ([0-9]+), "type": "([^"]+)".*"actor": "([^"]+)", "summary".*/\1 \2 \3/p'
}

# Succeeds when the latest probe of one cart type passed: probe_ok true|false
probe_ok() {
  raw_events "" | grep '"type": "checkout.probe"' | grep "\"discount\": $1" | tail -n 1 | grep -q '"ok": true'
}
