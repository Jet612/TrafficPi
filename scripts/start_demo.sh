#!/usr/bin/env bash
# Start the made-up street for the browser test: the vision service in demo mode and the API, on a
# data directory of their own, and wait until both answer.
#
#   E2E_PASSWORD=... scripts/start_demo.sh DATA_DIR LOG_DIR [API_PORT [VISION_PORT]]
#
# The ports are 8000 and 8001 unless given. Both services keep running when this returns, writing
# to LOG_DIR/vision.log and LOG_DIR/api.log. The exit status is 0 when both answer, 1 when one
# stops or has not answered within a minute (the end of its log is printed), 2 when a port is in
# use (the browser test rewrites the lines and zones of the database it runs against, so it must
# never meet a service that counts real traffic).
#
# CI's browser-test job and scripts/rehearse_ci.sh both start the services with this, so that they
# cannot differ. They once did: the rehearsal waited for both services and CI for the API alone,
# and CI failed because the vision service, which makes the demo's past before it listens, was
# not up yet when CI asked it.
set -euo pipefail
usage="usage: start_demo.sh DATA_DIR LOG_DIR [API_PORT [VISION_PORT]]"
data="${1:?$usage}"
logs="${2:?$usage}"
api_port="${3:-8000}"
vision_port="${4:-8001}"
: "${E2E_PASSWORD:?set E2E_PASSWORD}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
api_url="http://127.0.0.1:$api_port/api/health"
vision_url="http://127.0.0.1:$vision_port/health"

for port in "$api_port" "$vision_port"; do
  if ss -ltn 2>/dev/null | grep -qE ":$port\b"; then
    echo "port $port is in use: stop what uses it, or give other ports" >&2
    exit 2
  fi
done

mkdir -p "$logs"
cd "$root/backend"
printf '%s\n' "$E2E_PASSWORD" | uv run trafficpi-admin --data-dir "$data" set-password --password-stdin
# --history-days gives the demo three earlier days of made-up trails, so that the History page has
# days to move between.
nohup uv run trafficpi-vision run --demo --history-days 3 --data-dir "$data" --port "$vision_port" \
  > "$logs/vision.log" 2>&1 < /dev/null &
vision_pid=$!
# --mail-transport file: the daily email writes its mails into the data directory instead of
# sending them, so that the browser test can press "send a test" and nothing leaves the machine.
nohup uv run trafficpi-api --data-dir "$data" --host 127.0.0.1 --port "$api_port" \
  --vision-url "http://127.0.0.1:$vision_port" --mail-transport file > "$logs/api.log" 2>&1 < /dev/null &
api_pid=$!

fail() { # fail WHAT LOG: say which service is the trouble and how its log ended, and stop both
  echo "$1" >&2
  tail -n 20 "$2" | sed 's/^/    /' >&2
  kill "$vision_pid" "$api_pid" 2>/dev/null || true
  exit 1
}

for _ in $(seq 1 60); do
  kill -0 "$vision_pid" 2>/dev/null || fail "the vision service stopped before it answered" "$logs/vision.log"
  kill -0 "$api_pid" 2>/dev/null || fail "the API stopped before it answered" "$logs/api.log"
  if curl -fsS -o /dev/null "$api_url" 2>/dev/null && curl -fsS -o /dev/null "$vision_url" 2>/dev/null; then
    echo "both services answer:"
    curl -fsS "$api_url"; echo
    curl -fsS "$vision_url"; echo
    exit 0
  fi
  sleep 1
done
curl -fsS -o /dev/null "$vision_url" 2>/dev/null || fail "the vision service did not answer within 60 s" "$logs/vision.log"
fail "the API did not answer within 60 s" "$logs/api.log"
