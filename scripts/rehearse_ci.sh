#!/usr/bin/env bash
# shellcheck disable=SC2317,SC2329  # the job functions below are run by name, through `job` (older shellcheck calls this SC2317)
# Run CI's four jobs from a fresh clone of what is committed, the way a GitHub runner sees it.
#
#   scripts/rehearse_ci.sh              all four jobs (about 8 minutes)
#   scripts/rehearse_ci.sh --skip-e2e   without the browser test
#   scripts/rehearse_ci.sh --audit      also the weekly audit of the dependencies (scripts/audit_deps.sh)
#
# Why: this Pi has things a runner does not (Picamera2 and HailoRT, build output, files that
# .gitignore hides), so a test that passes here can fail there. The first push failed twice that
# way, and the third time the rehearsal had its own copy of how CI starts the services and waited
# for more than CI did (they share scripts/start_demo.sh now). Only committed files are tested, so
# commit first. The browser test starts the made-up street on ports 18000 and 18001, so it can run
# beside the services; it needs chromium-headless-shell (or CHROMIUM=/path/to/chrome: CI uses
# Google Chrome, and a full Chromium is nearer to it). The exit status is the number of failed jobs.
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
skip_e2e=0
audit=0
for argument in "$@"; do
  case "$argument" in
    --skip-e2e) skip_e2e=1 ;;
    --audit) audit=1 ;;
    *) echo "unknown option: $argument" >&2; exit 2 ;;
  esac
done

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
    && ../scripts/lint_shell.sh && uv run pyright && uv run pytest -q -p no:cacheprovider)
}

dashboard() { # types are checked before the build, as in CI: the build writes next-env.d.ts
  (cd dashboard && corepack enable && pnpm install --frozen-lockfile && pnpm lint \
    && pnpm typecheck && pnpm test && pnpm build)
}

api_types() {
  scripts/gen_api_types.sh \
    && git diff --exit-code -- dashboard/src/lib/openapi.json dashboard/src/lib/api-types.ts
}

browser_test() { # the services are started by the script that CI runs, on other ports
  E2E_PASSWORD="$password" scripts/start_demo.sh "$work/data" "$work" 18000 18001 || return 1
  local status=0
  (cd dashboard && E2E_BASE_URL=http://127.0.0.1:18000 E2E_PASSWORD="$password" E2E_DEMO=1 \
    E2E_EXPECT_LIVE=1 E2E_SHOTS="$work/shots" CHROMIUM="${CHROMIUM:-/usr/bin/chromium-headless-shell}" \
    node e2e/smoke.mjs) || status=1
  stop_demo
  return "$status"
}

audit_deps() { # after the two jobs above, which have installed what it looks at
  scripts/audit_deps.sh
}

job "backend (lint, types, tests)" backend
job "dashboard (lint, types, tests, build)" dashboard
job "dashboard types match the API" api_types
if (( skip_e2e )); then echo "skip  browser test"; else job "browser test" browser_test; fi
if (( audit )); then job "audit (weaknesses and licences)" audit_deps; fi

echo
if [[ $failed -eq 0 ]]; then echo "all jobs pass"; else echo "$failed job(s) failed"; fi
exit "$failed"
