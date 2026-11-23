#!/usr/bin/env bash
# Return the demo to a healthy shop. Deletes this project's incident and revision volumes.
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/common.sh

fail() {
  echo "PHOENIX reset failed: $*" >&2
  exit 1
}

[ -f .env ] || fail "there is no .env. Copy .env.example to .env first."
bad_rev=$(setting PHOENIX_BAD_REV)
[[ $bad_rev =~ ^[0-9a-f]{40}$ ]] || fail "PHOENIX_BAD_REV in .env must be the full SHA of the fault commit."

git fetch origin main fault/bad-checkout
git cat-file -e "$bad_rev^{commit}" 2>/dev/null || fail "commit $bad_rev is not in this repository."

# The switch takes its first healthy revision from the local main branch.
main=$(git rev-parse --verify main 2>/dev/null) || fail "there is no local main branch. Run: git switch main"
[ "$main" = "$(git rev-parse origin/main)" ] ||
  fail "local main is not origin/main. Run: git switch main && git pull --ff-only"

docker compose down -v
docker compose up -d

deadline=$((SECONDS + 90))
while [ $SECONDS -lt $deadline ]; do
  if curl -sf -m 2 "$BRAIN/health" >/dev/null && state=$(curl -sf -m 2 "$SWITCH/state"); then
    probe=$(raw_events "" | grep '"type": "checkout.probe"' | tail -n 1 || true)
    if [[ $probe == *'"ok": true'* ]]; then
      slot=$(sed -n 's/.*"active_slot": *"\([ab]\)".*/\1/p' <<<"$state" | tr ab AB)
      revision=$(sed -n 's/.*"revision": *"\([0-9a-f]\{7\}\).*/\1/p' <<<"$probe")
      echo "PHOENIX reset: healthy on slot $slot @ $revision"
      exit 0
    fi
  fi
  sleep 1
done
fail "checkout was not healthy within 90 seconds. Check: docker compose ps"
