#!/usr/bin/env bash
# Regenerate the dashboard's TypeScript types from the API's OpenAPI schema.
# Run after changing the API's routes or response models, and commit both generated files;
# CI fails when they are out of date.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
schema="$root/dashboard/src/lib/openapi.json"
(cd "$root/backend" && uv run python -m trafficpi.api.openapi "$schema")
(cd "$root/dashboard" && pnpm exec openapi-typescript src/lib/openapi.json -o src/lib/api-types.ts)
echo "updated dashboard/src/lib/openapi.json and api-types.ts"
