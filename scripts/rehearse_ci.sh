#!/usr/bin/env bash
# Run CI's four jobs from a fresh clone of what is committed, the way a GitHub runner sees it.
#
#   scripts/rehearse_ci.sh              all four jobs (about 8 minutes)
#   scripts/rehearse_ci.sh --skip-e2e   without the browser test
#
# Why: this Pi has things a runner does not (Picamera2 and HailoRT, build output, files that
# .gitignore hides), so a test that passes here can fail there. The first push failed twice that
# way. Only committed files are tested, so commit first. The browser test starts the made-up street
# on ports 18000 and 18001, so it can run beside the services; it needs chromium-headless-shell
# (or CHROMIUM=/path/to/chrome). The exit status is the number of failed jobs.
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
skip_e2e=0
[[ "${1:-}" == "--skip-e2e" ]] && skip_e2e=1

work="$(mktemp -d -t rehearse-ci.XXXXXX)"
failed=0
password="a password for the browser test"

stop_demo() { # whatever listens on the rehearsal's ports is the rehearsal's own
  local pids
  pids=$(ss -ltnp 2>/dev/null | grep -E ':(18000|18001)\b' | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u)
  for pid in $pids; do kill -TERM "$pid" 2>/dev/null; done
}
cleanup() {
  stop_demo
  if [[ $failed -eq 0 ]]; then rm -rf "$work"; else echo "kept for a look: $work"; fi
}
trap cleanup EXIT

if (( ! skip_e2e )) && ss -ltn 2>/dev/null | grep -qE ':(18000|18001)\b'; then
  echo "ports 18000 or 18001 are in use; stop what uses them, or use --skip-e2e" >&2
  exit 2
fi

git clone -q "$root" "$work/repo" || exit 2
cd "$work/repo" || exit 2
echo "rehearsing: $(git log --oneline -1)"
echo "(the $(git -C "$root" status --short | wc -l) uncommitted change(s) in the checkout are not tested)"

job() { # job NAME FUNCTION: run it, print PASS or FAIL and the end of its log
  local name=$1 function=$2 started=$SECONDS
  if "$function" > "$work/$name.log" 2>&1; then
    printf 'PASS  %-34s %3d s\n' "$name" $((SECONDS - started))
  else
    printf 'FAIL  %-34s %3d s\n' "$name" $((SECONDS - started))
    tail -20 "$work/$name.log" | sed 's/^/        /'
    failed=$((failed + 1))
  fi
}

backend() {
  (cd backend && uv sync --locked && uv run ruff check . && uv run ruff format --check . \
    && uv run pyright && uv run pytest -q -p no:cacheprovider)
}

dashboard() { # types are checked before the build, as in CI: the build writes next-env.d.ts
  (cd dashboard && corepack enable && pnpm install --frozen-lockfile && pnpm lint \
    && pnpm typecheck && pnpm test && pnpm build)
}

api_types() {
  scripts/gen_api_types.sh \
    && git diff --exit-code -- dashboard/src/lib/openapi.json dashboard/src/lib/api-types.ts
}

browser_test() {
  local data="$work/data"
  (cd backend && printf '%s\n' "$password" \
    | uv run trafficpi-admin --data-dir "$data" set-password --password-stdin) || return 1
  (cd backend && nohup uv run trafficpi-vision run --demo --data-dir "$data" --port 18001 \
    > "$work/vision.log" 2>&1 < /dev/null &)
  (cd backend && nohup uv run trafficpi-api --data-dir "$data" --host 127.0.0.1 --port 18000 \
    --vision-url http://127.0.0.1:18001 > "$work/api.log" 2>&1 < /dev/null &)
  for _ in $(seq 1 60); do
    curl -fsS http://127.0.0.1:18000/api/health > /dev/null 2>&1 \
      && curl -fsS http://127.0.0.1:18001/health > /dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS http://127.0.0.1:18001/health > /dev/null || { cat "$work/vision.log" "$work/api.log"; return 1; }
  local status=0
  (cd dashboard && E2E_BASE_URL=http://127.0.0.1:18000 E2E_PASSWORD="$password" E2E_DEMO=1 \
    E2E_EXPECT_LIVE=1 CHROMIUM="${CHROMIUM:-/usr/bin/chromium-headless-shell}" \
    node e2e/smoke.mjs) || status=1
  stop_demo
  return $status
}

job "backend (lint, types, tests)" backend
job "dashboard (lint, types, tests, build)" dashboard
job "dashboard types match the API" api_types
if (( skip_e2e )); then echo "skip  browser test"; else job "browser test" browser_test; fi

echo
if [[ $failed -eq 0 ]]; then echo "all jobs pass"; else echo "$failed job(s) failed"; fi
exit "$failed"
