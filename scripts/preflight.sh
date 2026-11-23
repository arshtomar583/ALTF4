#!/usr/bin/env bash
# GO / NO-GO before going on stage. Changes nothing except one tiny model call.
set -uo pipefail
cd "$(dirname "$0")/.."
. scripts/common.sh

problems=0
pass() { echo "✓ $1"; }
warn() { echo "⚠ $1. $2"; }
fail() {
  echo "✗ $1. Fix: $2"
  problems=$((problems + 1))
}
check() { # check "name" "fix" command...
  local name=$1 fix=$2
  shift 2
  if "$@" >/dev/null 2>&1; then pass "$name"; else fail "$name" "$fix"; fi
}

check "Docker is running" "start Docker" docker info
check "Docker Compose v2" "install the compose plugin" docker compose version

check ".env exists" "cp .env.example .env" test -f .env
if [ -n "$(setting GEMINI_API_KEY)$(setting ANTHROPIC_API_KEY)" ]; then
  pass "A model key is set"
else
  fail "No model key" "set GEMINI_API_KEY or ANTHROPIC_API_KEY in .env"
fi
if [ -n "$(setting TAVILY_API_KEY)" ]; then
  pass "TAVILY_API_KEY is set"
else
  warn "No TAVILY_API_KEY" "Search will use the rate-limited keyless mode"
fi

bad_rev=$(setting PHOENIX_BAD_REV)
if [[ $bad_rev =~ ^[0-9a-f]{40}$ ]] && git cat-file -e "$bad_rev^{commit}" 2>/dev/null; then
  pass "The fault commit ${bad_rev:0:7} is here"
else
  fail "PHOENIX_BAD_REV is not a commit here" "git fetch origin fault/bad-checkout, then put its full SHA in .env"
fi
if git rev-parse -q --verify main >/dev/null && ! git merge-base --is-ancestor "${bad_rev:-none}" main 2>/dev/null; then
  pass "The fault is not on main"
else
  fail "The fault is on main, or there is no local main" "git switch main; never merge fault/bad-checkout"
fi
check "origin/fault/bad-checkout is fetched" "git fetch origin fault/bad-checkout" git rev-parse -q --verify origin/fault/bad-checkout
git fetch -q origin main 2>/dev/null || warn "Could not reach GitHub" "Comparing with the last fetched main"
if [ "$(git rev-parse main 2>/dev/null)" = "$(git rev-parse origin/main 2>/dev/null)" ]; then
  pass "Local main matches origin/main"
else
  fail "Local main differs from origin/main" "git switch main && git pull --ff-only"
fi

published=$(docker compose ps --format '{{.Ports}}' 2>/dev/null)
for item in "switch PHOENIX_SWITCH_PORT 8080" "brain PHOENIX_BRAIN_PORT 8000" "dashboard PHOENIX_DASHBOARD_PORT 3000"; do
  read -r name variable default <<<"$item"
  port=$(setting "$variable" "$default")
  if [[ $published == *":$port->"* ]]; then
    pass "Port $port is held by the $name"
  elif (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    fail "Port $port is taken by another app" "stop that app, or set $variable in .env and rebuild"
  else
    pass "Port $port is free for the $name"
  fi
done

check "Brain answers" "./scripts/reset.sh, then docker compose logs brain" curl -sf -m 3 "$BRAIN/health"
check "Version switch answers" "./scripts/reset.sh, then docker compose logs version-switch" curl -sf -m 3 "$SWITCH/state"
check "Plain checkout passes" "./scripts/reset.sh" probe_ok false
check "SAVE10 checkout passes" "./scripts/reset.sh" probe_ok true
if ! incident=$(curl -sf -m 3 "$BRAIN/incidents/current"); then
  fail "Cannot tell whether an incident is open" "start the stack with ./scripts/reset.sh"
elif [ "$incident" = null ]; then
  pass "No incident is open"
else
  fail "An incident is open" "finish it on the dashboard, or run ./scripts/reset.sh"
fi

if model=$(timeout 15 docker compose exec -T brain python -c 'import asyncio; from brain import llm; print(asyncio.run(llm.ping()))' 2>/dev/null); then
  pass "Live model call works ($model)"
else
  fail "The live model call failed within 15s" "check the key and its quota; see Troubleshooting in README.md"
fi

echo
if [ $problems -eq 0 ]; then
  echo "========  GO  ========"
else
  echo "====  NO-GO ($problems problems)  ===="
  exit 1
fi
