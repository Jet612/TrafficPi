#!/usr/bin/env bash
# Run shellcheck over the shell scripts of the repository. CI's backend job and
# scripts/rehearse_ci.sh both run this, so that they cannot check different things. It always runs
# the one version pinned below, fetched with uv as a development tool (the shellcheck-py package,
# which adds nothing to the project's dependencies), and never the shellcheck that happens to be
# installed: the runners' version is older than this Pi's and the two disagreed once (2026-10-09:
# 0.9 and 0.10 report SC2317 for what 0.11 calls SC2329, so CI failed on a script the rehearsal
# had passed). Raise the pin on purpose, and run the scripts under the older releases when you do.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
pinned=0.11.0.1
uvx --from "shellcheck-py==$pinned" shellcheck -x scripts/*.sh
echo "shellcheck $pinned: scripts/*.sh are clean"
