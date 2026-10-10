## What this changes, and why

<!-- One or two sentences. Link the issue or the decision in plan.md (D…) it follows. -->

## How it was tested

<!-- Tick what applies, and say on what: a laptop, the made-up street (`run --demo`), the real camera. -->

- [ ] Tests added or changed (a fix has one that failed before it)
- [ ] `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest` (backend)
- [ ] `pnpm lint && pnpm typecheck && pnpm test` (dashboard)
- [ ] The browser test (`dashboard/e2e/smoke.mjs`) against the made-up street, if a page changed
- [ ] `scripts/gen_api_types.sh` run, if a route or a response model changed
- [ ] `scripts/rehearse_ci.sh` passed

## Not tested

<!-- What you did not or could not try (the real camera, a phone, another browser). -->

## Notes kept true

- [ ] plan.md (a decision, or its log), todo.md and the docs say what is now so
- [ ] Nothing here reads a licence plate, identifies a face or a person, or weakens the "not for enforcement" wording
