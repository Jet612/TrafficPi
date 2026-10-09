#!/usr/bin/env bash
# Run shellcheck over the shell scripts of the repository. CI's backend job and
# scripts/rehearse_ci.sh both run this, so that they cannot check different things. GitHub's
# runners have shellcheck; on a machine without it (this Pi) it is fetched with uv as a development
# tool (the shellcheck-py package), which adds nothing to the project's dependencies.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if command -v shellcheck >/dev/null 2>&1; then
  check=(shellcheck)
else
  check=(uvx --from shellcheck-py shellcheck)
fi
"${check[@]}" -x scripts/*.sh
echo "shellcheck: scripts/*.sh are clean"
