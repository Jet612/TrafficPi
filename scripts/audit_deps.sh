#!/usr/bin/env bash
# shellcheck disable=SC2317,SC2329  # the check functions below are run by name, through `check` (older shellcheck calls this SC2317)
# Look for known weaknesses and unwelcome licences in the locked dependencies (plan.md M8 step 8).
#
#   scripts/audit_deps.sh              all three checks
#   scripts/audit_deps.sh python       known weaknesses in the locked Python packages (pip-audit)
#   scripts/audit_deps.sh dashboard    known weaknesses in the dashboard's packages (pnpm audit)
#   scripts/audit_deps.sh licences     licences against plan.md D16 (scripts/check_licenses.py)
#
# It needs the network (the advisory databases of PyPI and npm), and the locked packages installed:
# `uv sync --locked` in backend/ and `pnpm install --frozen-lockfile` in dashboard/. The exit status
# is the number of checks that failed. The weekly workflow .github/workflows/audit.yml runs this, and
# scripts/rehearse_ci.sh --audit runs it from a fresh clone; it is not part of the checks that every
# push must pass, because a new advisory would turn an unrelated push red.
set -uo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"
want="${1:-all}"
case "$want" in all|python|dashboard|licences) ;; -h|--help)
  awk 'NR > 1 && /^# shellcheck/ { next } NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"; exit 0 ;;
  *) echo "unknown check: $want (python, dashboard, licences or all)" >&2; exit 2 ;; esac

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
failed=0

python_weaknesses() {
  (cd "$root/backend" && uv export --locked --no-dev --no-emit-project -o "$tmp/requirements.txt" >/dev/null) \
    && uvx --from pip-audit pip-audit -r "$tmp/requirements.txt" --require-hashes --no-deps \
         --disable-pip --progress-spinner off
}

dashboard_weaknesses() {
  (cd "$root/dashboard" && pnpm audit --prod)
}

licences() {
  (cd "$root/backend" && uv export --locked --no-dev --no-emit-project -o "$tmp/requirements.txt" >/dev/null) \
    && "$root/backend/.venv/bin/python" "$root/scripts/check_licenses.py" python "$tmp/requirements.txt" \
    && (cd "$root/dashboard" && pnpm licenses list --prod --json) | python3 "$root/scripts/check_licenses.py" pnpm
}

check() { # check NAME FUNCTION: run it, say PASS or FAIL, and show what it said when it failed
  local name=$1 function=$2
  if "$function" > "$tmp/$function.log" 2>&1; then
    printf 'PASS  %s\n' "$name"
  else
    printf 'FAIL  %s\n' "$name"
    sed 's/^/        /' "$tmp/$function.log"
    failed=$((failed + 1))
  fi
}

[[ "$want" == all || "$want" == python ]] && check "known weaknesses in the locked Python packages" python_weaknesses
[[ "$want" == all || "$want" == dashboard ]] && check "known weaknesses in the dashboard's packages" dashboard_weaknesses
[[ "$want" == all || "$want" == licences ]] && check "licences against plan.md D16" licences
exit "$failed"
