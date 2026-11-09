# Contributing to TrafficPi

Thanks for looking. TrafficPi is a small project with one maintainer, so a short note before a big change saves everyone's time: open an issue first for anything beyond a fix.

## What this project is, and is not

TrafficPi counts vehicles, judges stop-sign behaviour and estimates speeds from one camera on a Raspberry Pi 5 with a Hailo accelerator. Its [non-goals](README.md#privacy-and-accuracy) decide what will not be merged:

- **No licence-plate reading and no face or person identification**, whatever the use.
- **No enforcement.** It is not a certified speed-measurement device and a result must not be used as evidence against anyone; the pages and the README say so, and a change must not weaken that.
- **No cloud service is required.** Video stays on the device.

The [README](README.md) shows the architecture, and [docs/](docs/) has the rest: [dev-setup.md](docs/dev-setup.md) (how the parts fit, and why the toolchain is what it is), [security.md](docs/security.md), [speed-calibration.md](docs/speed-calibration.md) and [accuracy.md](docs/accuracy.md). Read what covers the part you want to change before you change it; if you think a design choice is wrong, say so in the issue.

## You do not need the hardware for most work

The rule engine is pure functions over tracked vehicles, and `trafficpi-vision run --demo` plays a made-up street through the real tracker, counting lines, database and dashboard, so nearly everything can be developed and tested on a laptop. Only the camera and accelerator code (`vision/sources.py`, `vision/detector.py`) needs the Pi; its tests are skipped elsewhere.

Setting up, the commands that work on the Pi, the toolchain and the traps found on the way are in [docs/dev-setup.md](docs/dev-setup.md). In short:

```bash
# backend: Python 3.13 and uv
cd backend
uv sync --locked
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run pytest                       # tests that need the camera or accelerator are skipped

# dashboard: Node 22 and pnpm (Corepack supplies the version package.json pins)
cd dashboard
pnpm install --frozen-lockfile
pnpm lint && pnpm typecheck && pnpm test
pnpm build                          # a static export in dashboard/out

# the made-up street, and the browser test against it (docs/dev-setup.md)
E2E_PASSWORD=... scripts/start_demo.sh /tmp/demo/data /tmp/demo/logs
E2E_PASSWORD=... E2E_DEMO=1 node dashboard/e2e/smoke.mjs
```

[scripts/rehearse_ci.sh](scripts/rehearse_ci.sh) runs CI's four jobs from a fresh clone of what you have committed (about eight minutes). A change that passes in your checkout can still fail on GitHub, because your checkout has files a runner does not; run it before you push.

## Making a change

- **Tests come with the change.** A fix starts with a test that fails for the reason you are fixing; a feature has tests of what it does and of what it must refuse. The rules are checked against recorded or simulated drives, not only against hand-made numbers (see `backend/tests/camera_sim.py` and `backend/tests/fakes.py`).
- **The API's types are generated.** After changing a route or a response model run `scripts/gen_api_types.sh` and commit `dashboard/src/lib/openapi.json` and `api-types.ts`; CI fails when they are out of date.
- **Style.** Ruff (line length 100) and Pyright for Python; ESLint and TypeScript for the dashboard. Write comments the way the code around yours does: say why, not what.
- **Words on screen.** One name for each idea (`dashboard/src/lib/stops.ts` has the outcomes, `lib/format.ts` the dates and times). A hint beside an option says what the option does and never why anyone would use it; docs/dev-setup.md ("Explaining an option") has the rule.
- **Keep the docs true.** A change to what a page says, a command, a setting or a limit updates the README or the page in `docs/` that describes it, in the same pull request. [CLAUDE.md](CLAUDE.md) has the rules for an AI assistant working in the checkout, and the traps of the development Pi.
- **Commit messages** name the area and say what changed: `dashboard: the Review page keeps a pass you have just reviewed`.
- **Do not run heavy jobs beside a live camera.** If you develop on the Pi that is also counting traffic, queue them as docs/dev-setup.md says ("Heavy jobs share the Pi with the camera").

## Pull requests

Open one against `main` with the template filled in: what changed and why, how you tested it (and on what: a laptop, the made-up street, the real camera), and what you did not test. Small, single-purpose pull requests are reviewed quickly. CI must pass.

## Reporting a problem

Use the issue forms. For a **security** problem, do not open a public issue: use GitHub's private vulnerability reporting on the repository's Security tab. [docs/security.md](docs/security.md) says what is protected from whom, and what is not.

A bug report is most useful with the version (`git log -1 --oneline`), what the System page says, and the relevant lines of `journalctl -u trafficpi-vision -u trafficpi-api`. Remove anything private first: the journal can hold a tunnel token or a mail key, and a recording of a real road can show people.

## Licence

By contributing you agree that your work is released under the [MIT License](LICENSE). Dependencies must be permissively licensed (MIT, BSD, Apache-2.0, ISC and the like); `scripts/audit_deps.sh` checks that. Do not commit a model file: the models have licenses of their own ([README, License](README.md#license)), and they are downloaded or installed, never kept in the repository.
