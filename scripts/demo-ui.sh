#!/usr/bin/env bash
# Serve the dashboard in production mode, without Docker. Run "docker compose stop dashboard" first if it is up.
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/common.sh

port=$(setting PHOENIX_DASHBOARD_PORT 3000)
if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
  echo "Port $port is taken. Stop that app, or set PHOENIX_DASHBOARD_PORT." >&2
  exit 1
fi
NEXT_PUBLIC_BRAIN_URL=$(setting NEXT_PUBLIC_BRAIN_URL "http://$BRAIN")
export NEXT_PUBLIC_BRAIN_URL NEXT_TELEMETRY_DISABLED=1
echo "Dashboard: http://localhost:$port (brain: $NEXT_PUBLIC_BRAIN_URL)"
cd apps/dashboard
npm run build
exec npm start -- -p "$port"
