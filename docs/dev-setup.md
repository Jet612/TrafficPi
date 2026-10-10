# Development setup (Raspberry Pi 5)

The exact steps that brought up the development machine (milestone M0), with the results they produced. Verified on 2026-10-08. The installer in M8 will be derived from this page.

| | |
|---|---|
| Board | Raspberry Pi 5 Model B, 8 GB |
| OS | Raspberry Pi OS Lite 64-bit, Debian 13 (Trixie), kernel 6.18.50+rpt-rpi-2712, Python 3.13.5 |
| Accelerator | Raspberry Pi AI HAT+, **Hailo-8L (13 TOPS)** |
| Camera | Camera Module 3 (IMX708) |

## 1. System packages

```bash
sudo apt-get update
sudo rpi-eeprom-update -a        # stages a bootloader update; it applies at the next reboot
sudo apt-get install -y --no-install-recommends \
  dkms hailort hailort-pcie-driver python3-hailort hailo-models \
  python3-picamera2 python3-numpy sqlite3 ffmpeg
```

This is 187 packages (335 MB download, 820 MB on disk). It does not use the `hailo-all` bundle: that adds about 650 packages and 2.3 GB (TAPPAS and GStreamer) which TrafficPi's Python route does not use. If the `rpicam-apps` post-processing route is ever needed, install `rpicam-apps-hailo-postprocess` (or `hailo-all`) then. `--no-install-recommends` keeps Picamera2's Qt and OpenGL preview dependencies off a headless system.

The Hailo packages are pinned so an upgrade can't change driver, runtime and models independently:

```bash
sudo apt-mark hold hailort hailort-pcie-driver python3-hailort hailo-models
# to upgrade them together later: sudo apt-mark unhold <the four packages>, upgrade, re-test, hold again
```

Installed versions: HailoRT 4.23.0, `hailort-pcie-driver` 4.23.0, `hailo-models` 1.0.0-2, `python3-picamera2` 0.3.37, libcamera 0.7.2, NumPy 2.2.4, PyAV 14.2.0, ffmpeg 7.1.5, sqlite3 3.46.1, dkms 3.2.2.

## 2. Verify the accelerator

```bash
dkms status                        # hailo_pci/4.23.0, <kernel>, aarch64: installed
lsmod | grep hailo                 # hailo_pci is loaded (a udev rule loads it at boot)
ls -l /dev/hailo0                  # crw-rw-rw-
hailortcli fw-control identify     # "Device Architecture: HAILO8L" (13 TOPS) or "HAILO8" (26 TOPS)
lspci | grep -i hailo              # find the PCIe address (0001:01:00.0 on this machine)
sudo lspci -vv -s 0001:01:00.0 | grep -E 'LnkCap|LnkSta'   # LnkSta: Speed 8GT/s (Gen 3), Width x1
```

`lspci` and `Board Name` both say "Hailo-8" even for the 13 TOPS Hailo-8L. Only `Device Architecture` tells them apart, and it decides which model files to use (`*_h8l.hef` or `*_h8.hef` in `/usr/share/hailo-models/`). On this machine the width shows `x1 (downgraded)`: the Pi 5 exposes a single PCIe lane, which is expected.

## 3. Verify the camera and the Python bindings

```bash
rpicam-hello --list-cameras
python3 -I -c "from picamera2.devices import Hailo; import hailo_platform, numpy, av; print('ok')"
```

Use the system Python (`/usr/bin/python3`): Picamera2, libcamera and HailoRT are apt packages. A project virtualenv has to be created with `--system-site-packages` to see them.

## 4. Smoke test

After a reboot, `scripts/check_boot.sh` runs the whole set of checks below (module, driver, device node, accelerator, bootloader, camera, PCIe link, the reserved RAM page and, last, a 10 s smoke test, or a check of the running vision service when the systemd services are up, since they own the camera) and prints PASS or FAIL for each.

```bash
python3 -I scripts/smoke_hailo.py --source blank --seconds 10   # accelerator only, no camera
python3 -I scripts/smoke_hailo.py --seconds 60                  # camera + accelerator
```

The script saves nothing to disk. `Hailo.run(frame)` returns a list of 80 arrays (one per COCO class), each of shape `(N, 5)` with rows `[y_min, x_min, y_max, x_max, score]` normalized to 0–1. The detector runs non-maximum suppression on the chip.

Reference results with `yolov8s_h8l.hef` (640×640 input), measured in the evening on a static scene:

| Run | Throughput | Capture wait | Inference mean / p95 | SoC temperature |
|---|---|---|---|---|
| Blank frames, 10 s | 56.8 fps | n/a | 17.6 ms / 17.8 ms | 61 → 60 °C |
| Camera, 60 s | 30.0 fps (sensor-limited) | 15.8 ms | 17.4 ms / 17.7 ms | 61 → 60 °C |

No throttling occurred (`vcgencmd get_throttled` was `0x0`).

## 5. User-space toolchain

Installed under `~/.local` with checksums verified. Make sure `~/.local/bin` is on `PATH`.

```bash
# uv (Python project manager)
cd "$(mktemp -d)"
base=https://github.com/astral-sh/uv/releases/latest/download
curl -fsSLO "$base/uv-aarch64-unknown-linux-gnu.tar.gz"
curl -fsSLO "$base/uv-aarch64-unknown-linux-gnu.tar.gz.sha256"
sha256sum -c uv-aarch64-unknown-linux-gnu.tar.gz.sha256
tar -xzf uv-aarch64-unknown-linux-gnu.tar.gz
install -m 0755 uv-aarch64-unknown-linux-gnu/uv uv-aarch64-unknown-linux-gnu/uvx ~/.local/bin/

# Node.js 22 LTS ("Jod"). Debian's own nodejs is v20 (past upstream end-of-life). Node 24 works too
# (the crashes first seen with it were this board's defective RAM, see Troubleshooting).

cd "$(mktemp -d)"
v=$(curl -fsSL https://nodejs.org/dist/index.json | python3 -c 'import json,sys; print(next(r["version"] for r in json.load(sys.stdin) if r["lts"] == "Jod"))')
curl -fsSLO "https://nodejs.org/dist/$v/node-$v-linux-arm64.tar.xz"
curl -fsSLO "https://nodejs.org/dist/$v/SHASUMS256.txt"
grep " node-$v-linux-arm64.tar.xz\$" SHASUMS256.txt | sha256sum -c -
mkdir -p ~/.local/share/node && tar -xJf "node-$v-linux-arm64.tar.xz" -C ~/.local/share/node
ln -sfn ~/.local/share/node/node-$v-linux-arm64 ~/.local/share/node/current
for b in node npm npx corepack; do ln -sfn ~/.local/share/node/current/bin/$b ~/.local/bin/$b; done

# pnpm through Corepack
corepack enable --install-directory ~/.local/bin pnpm
corepack prepare pnpm@latest --activate
```

Installed: uv 0.12.24, Node 22.23.3, npm 10.9.9, pnpm 12.10.1. Each project pins its own pnpm version through `packageManager` in `package.json`.

## 6. The backend

```bash
cd backend
uv venv --system-site-packages     # once; the venv must see the apt packages (Picamera2, HailoRT)
uv sync                            # install the locked dependencies
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run pytest                      # hardware-free tests
uv run pytest --hardware           # also the camera and accelerator tests (nothing else may be using them)

uv run trafficpi-vision dry-run --seconds 30        # speed, latency, CPU, memory, temperature
uv run trafficpi-vision snapshot --out /tmp/x.jpg   # one frame with the detections drawn on it
uv run trafficpi-vision record --seconds 60         # a clip and its detections under data/clips/<time>/
```

Runtime files (the database, snapshots, clips, detection logs) go under `$TRAFFICPI_DATA_DIR`, by default `backend/data` whatever directory a command is run from (the services use the same place), which is git-ignored. Recordings show whatever the camera sees, so keep them out of the repository.

### Recording, replaying and counting

```bash
# Record a clip and its detections (weak detections are kept for the tracker).
uv run trafficpi-vision record --seconds 120 --name corner-am
#   -> data/clips/corner-am/clip.mp4, detections.jsonl

# Counting lines: x, y as fractions of the frame, y pointing down. Crossing from the left of the
# line's direction a -> b to its right counts +1.
cat > data/clips/corner-am/lines.json <<'EOF'
{"lines": [{"id": "stop-a", "name": "Side street", "a": [0.52, 0.37], "b": [0.68, 0.37],
            "classes": ["car", "truck", "bus"]}]}
EOF

uv run trafficpi-vision replay   data/clips/corner-am/detections.jsonl --lines data/clips/corner-am/lines.json
uv run trafficpi-vision annotate data/clips/corner-am --lines data/clips/corner-am/lines.json
#   -> annotated.mp4 and event-stills/*.jpg (one per count, to check by eye)

# Compare models on the same clip: run each over it (--seconds limits how long), then compare the logs
# frame by frame. Boxes are matched by overlap; the output shows counts, mean scores and how the
# scores of the shared objects differ.
D=data/clips/corner-am
uv run trafficpi-vision redetect $D/clip.mp4 --model yolov8s --out $D/det-yolov8s.jsonl
uv run trafficpi-vision redetect $D/clip.mp4 --model yolov6n --out $D/det-yolov6n.jsonl
uv run trafficpi-vision redetect $D/clip.mp4 --hef /usr/share/hailo-models/yolox_s_leaky_h8l_rpi.hef --out $D/det-yolox.jsonl
uv run trafficpi-vision compare $D/det-yolov8s.jsonl $D/det-yolov6n.jsonl --min-score 0.25
```

The colour order matters: the detectors expect RGB, and `--channel-order bgr` on the same clip found a third as many detections with 0.10 lower scores. Raspberry Pi's own Hailo example feeds Picamera2's `RGB888` stream, which is BGR in memory. TrafficPi asks for Picamera2's `BGR888` format, which is RGB in memory (`CameraSettings.channel_order`).

`replay` needs no camera or accelerator and gives the same answer every time, so it is also how tracker and counting changes are tested against real recordings.

### Running the services by hand

The system is two processes (plan.md D1). The vision service owns the camera and the accelerator; the API serves the dashboard and talks to the vision service on localhost.

```bash
cd backend
uv run trafficpi-admin set-password     # once: creates the user "admin" (asks twice; 10 characters at least)
uv run trafficpi-vision run             # camera, detector, tracking, counting; live view on 127.0.0.1:8001
uv run trafficpi-api                    # dashboard and API on 0.0.0.0:8000 (in a second terminal)
```

Both read `$TRAFFICPI_DATA_DIR` (default `backend/data`, the same for the commands and the services) and keep the database there as `trafficpi.db`; `--data-dir` overrides it. Stop either with Ctrl-C or SIGTERM. Counting lines are saved through the API (`PUT /api/profile`, the same JSON as `lines.json` above) and picked up by the running vision service without a restart.

- **One process at a time.** Only one process can use the camera and only one the accelerator, so stop `trafficpi-vision run` before `dry-run`, `snapshot` or `record`. A second process stops at once with "cannot open the Hailo accelerator".
- **A broken camera does not stop the service.** It stays up, shows `camera-offline` with the reason, and tries again every 5 s (a read that gets no frame for 5 s counts as a failure). After 5 minutes without a camera it exits with an error, so that systemd restarts it with libcamera in a clean state.
- **Developing without the camera or traffic.** `uv run trafficpi-vision run --replay data/clips/<name>/clip.mp4` plays a recorded clip in a loop as if it were live. Its counts go to `data/replay/` (never into the real database); point the API at the same place with `uv run trafficpi-admin --data-dir data/replay set-password` and `uv run trafficpi-api --data-dir data/replay`.
- **A made-up street.** `uv run trafficpi-vision run --demo` needs neither the camera nor the accelerator, so it also works on a laptop. Cars, trucks, buses and bicycles drive along a drawn road through the real tracker, counting lines and database; counts go to `data/demo` (use `--data-dir data/demo` for the API and `trafficpi-admin`). `--history-days 7` also makes up trails for the seven days before today (about 5,000 on a weekday, busiest at 8 and 17 o'clock) if the demo has none yet, so that the History page has days to move between at once; the browser test needs it (CI uses 3). Draw a line across the road in the dashboard's Lines and zones editor and the vehicles get counted. The browser tests and the screenshots use it. To run it beside the real services (which hold ports 8000 and 8001 and the camera), give it other ports: `uv run trafficpi-vision run --demo --data-dir data/demo --port 18001` and `uv run trafficpi-api --host 127.0.0.1 --port 18000 --vision-url http://127.0.0.1:18001 --data-dir data/demo --mail-transport file` (the last flag makes the daily email write its mails to `data/demo/outbox` instead of sending them, so that nothing a demo does can leave the machine; `scripts/start_demo.sh` does all of this).
- **As services.** `scripts/install_services.sh` installs `trafficpi-vision` and `trafficpi-api` as systemd units for this checkout (`--enable` also starts them now and at every boot; `--remove` takes them out again; `--data-dir` and `--user` override the defaults, which are you and `backend/data`). **On this Pi they have been enabled since 2026-10-09**, so they own the camera and the accelerator: `sudo systemctl stop trafficpi-vision` before a hand-run `record`, `snapshot` or `dry-run`, and start it again afterwards. After changing the units or their data directory, stop both before running the script again (`enable --now` does not restart a service that is running). Logs: `journalctl -u trafficpi-vision -u trafficpi-api -f`.
- **A reboot every day.** `scripts/install_services.sh --enable --reboot-at 03:00` also installs `trafficpi-reboot.timer`, which starts `trafficpi-reboot.service` (`systemctl reboot`) every day at that time on the Pi's own clock (`timedatectl`; 03:00 exists on every day of the year in New York, the days the clocks change included). **On this Pi it has been on since 2026-10-09**, at the owner's request. The services are stopped first and start again with the boot; expect roughly a minute in which nothing is counted (the boot takes about 15 s and the vision service is up a few seconds after that), and **any terminal or Claude session open at that time ends**. A Pi that came up less than ten minutes before is left alone, so a power cut just before 03:00 cannot cause a second reboot, and a reboot that fell while the Pi was off is not made up for. `systemctl list-timers trafficpi-reboot.timer` shows the next one; `scripts/install_services.sh --reboot-off` takes it out again (`--remove` does too, with the services). A kernel that `apt upgrade` installs is first booted by the next of these reboots, and the Hailo driver is built per kernel (DKMS): the morning after an upgrade, run `scripts/check_boot.sh`.
- **A journal that survives the reboot.** Raspberry Pi OS keeps the journal in RAM (`/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf`), so after the nightly reboot `journalctl -b -1` shows nothing: the log of how the Pi went down, and of the services before it, is gone. `scripts/install_services.sh --persistent-journal` installs `/etc/systemd/journald.conf.d/50-trafficpi.conf` (from `deploy/journald/`), which keeps it on the disk, at most 256 MB and a month of it, and restarts `systemd-journald` (the services keep running and keep logging). **On this Pi it has been on since 2026-10-09 14:23.** `--journal-off` puts the Raspberry Pi OS setting back. Measured here: in steady state a few MB a day, and each start of the vision service adds a burst of the Hailo driver's warnings (dozens of stack traces of about 36 lines each, see Troubleshooting; this boot's 584 were 83% of the bytes it wrote); the services run as the login user, so journald keeps their lines in `user-1000.journal`, which `journalctl -u trafficpi-vision` reads along with the system journal. The morning after the first nightly reboot: `journalctl -b -1 -n 60` (how the last boot ended) and `journalctl -b -u trafficpi-vision` (how this one began).
- **Trails.** The vision service saves the path of every vehicle that moves, when its track ends (plan.md D21, M4b). See what is stored with `sqlite3 backend/data/trafficpi.db "select count(*), datetime(min(started_at)/1000, 'unixepoch', 'localtime'), datetime(max(ended_at)/1000, 'unixepoch', 'localtime'), sum(length(path)) from trails"`; `curl -s http://127.0.0.1:8001/health` has `trails_recorded` for the time since the service started. They are saved for vehicles and not for people, and kept for 365 days; `GET` and `PUT /api/settings` change that (`trails_enabled`, `trails_people`, `trails_keep_days`; a `PUT` must give every member), and the Settings card on the Setup page does. The History page's Trails card shows them: a day by the hour, any hours of it as paths (at most 600, evenly chosen, in their kinds' colours) or as a heat map (all of them), with the lines and zones over the camera picture, each kind switched on or off, played hour by hour, or saved as a PNG; the API behind it is `GET /api/trails/day` and `GET /api/trails` (at most 32 days a request; an even sample of at most 5,000 paths, with the real total). A parked car is not a trail (its points do not spread), and nothing is saved from an exclusion zone.
- **Speeds (plan.md M6).** A profile may hold a `calibration`: a rectangle on the road with its width and length, and known distances that test it (`backend/trafficpi/core/ground.py` holds the maths; `docs/speed-calibration.md` is how to draw one). With one, the speed meter (`vision/speedmeter.py`) puts every tracked vehicle on the road in metres and gives it a speed over the last second where the calibration can be believed; counts and stop passes carry the speeds. The tests need no camera: `backend/tests/camera_sim.py` is an ideal pinhole over a flat road, with vehicles as boxes of known speed, and `tests/test_engine_speed.py` and `tests/test_speedmeter.py` drive them through the real tracker with a shaking box. A recorded detection log can try a calibration: `uv run trafficpi-vision replay LOG --lines PROFILE.json` prints each count's speed in metres a second when the profile has a `calibration`, and `annotate` writes it on the vehicles. `uv run trafficpi-admin speed-check FILE` compares what the camera measured with the vehicles you drove past at a known speed (the file is a line to a pass, `time, speed`) and prints the error: that is the check the pages wait for. Each stored speed carries the name of the calibration it was measured under (`cal`), and the sums, the Speeds section and `speed-check` use only the speeds of the calibration saved now (`Store.current_calibration()`). The accuracy grid is worked out in 0.1 s (`estimate_accuracy`), kept between profile reloads and recomputed by the API for the editor (`POST /api/calibration/report`).
- **Evidence.** For each stop-sign pass that needs review (rolled through, did not stop, unclear; a clean stop gets none) the vision service keeps a picture and a clip of about nine seconds (6 before the stop line, 3 after) in `<data dir>/evidence/<UTC day>/stop-<event id>.jpg` and `.mp4` (plan.md D24, M5 step 5). While a stop sign is set up, the last twelve seconds of frames live only in memory, as 640 by 360 JPEGs at 15 a second (about 0.05 cores); the clip is made with the system's `ffmpeg` (libx264; `sudo apt install ffmpeg` if `which ffmpeg` is empty: without it there is a picture and no clip, and one warning in the journal). The database names the files (`events.evidence_path`, and `details.clip` in the event's JSON); `GET /api/stops` gives each pass's `snapshot_url` and `clip_url` (null when the file is gone), and `GET /api/stops/<id>/snapshot.jpg` and `/clip.mp4` serve them to a logged-in browser. `curl -s http://127.0.0.1:8001/health` has `evidence_written`, `evidence_waiting` and `evidence_frames_dropped` for the time since the service started (the last is frames the recorder had no room for, which would be a gap in a clip; it is 0 when all is well). It is on from the start and costs nothing until a stop sign is set up; `GET` and `PUT /api/settings` (`evidence_enabled`, `evidence_keep_days`, 30, `evidence_max_mb`, 2048, and `evidence_rolling_clip_hours`, 3, and `evidence_did_not_stop_clip_hours`, 48) and the Settings card change it, and the service picks it up at once. At start, on every reload and then every ten minutes, a clip older than its pass's outcome allows is deleted (3 hours for a roll, 48 for a pass that did not stop; the outcome is read from the database by the event id in the file's name, and a clip whose outcome is not known is left to the days), then any file older than the days, then the oldest until the rest fit, also while evidence is switched off. The pictures, the rows and so the counts stay. Try it without a camera: run the made-up street, draw a stop sign across its near lane in the editor (the demo's cars do not stop), and the History page's Stop signs card shows each pass with its picture a few seconds later.
- **Checking it from the shell.**

```bash
curl -s http://127.0.0.1:8001/health        # the vision service, no login (this machine only)
curl -s -c jar -H 'Content-Type: application/json' -d '{"username":"admin","password":"..."}' \
     http://127.0.0.1:8000/api/auth/login
curl -s -b jar http://127.0.0.1:8000/api/status
curl -s -b jar -o snapshot.jpg http://127.0.0.1:8000/api/snapshot.jpg
```

## 7. The dashboard

A Next.js app (App Router) exported as static files, with Tailwind CSS 4 and Recharts. Nothing runs on the Pi at run time but the API, which serves `dashboard/out`. It has five pages, each a real address with its own folder in the export: Live (`/`), History (`/history/`), Data (`/data/`), Setup (`/setup/`) and System (`/system/`). The shared layout (`src/app/(app)/layout.tsx`, `components/app.tsx`) holds the session, the live socket and today's numbers, so moving between pages reconnects nothing; a page with unsaved work calls `useUnsavedWarning` and the navigation then asks before leaving.

```bash
cd dashboard
pnpm install --frozen-lockfile        # Node 22; Corepack supplies the pnpm version that package.json pins
pnpm lint && pnpm typecheck && pnpm test
pnpm build                            # writes dashboard/out; trafficpi-api serves it at /
```

- **The Data page** (plan.md D25). The shares of the vehicles that stopped, rolled through, did not stop or were unclear at the stop signs (and how far they moved since the days before), the busiest hours and days, the hours and kinds with the lowest stop rate, how long a typical stop lasts and how slowly a typical roll goes, how many passes have been reviewed, and the traffic counted: by hour of the day, day, day of the week, kind and counting line. One period (today, 7, 30 or 90 days, all time; the choice is remembered) covers all of it, and every chart has a table view. The API does the sums, in the camera's time zone: `GET /api/stats/stops?days=&sign=` and `GET /api/stats/traffic?days=` (`days` is the number of local days ending today, `0` for everything since the first pass or count, a year at most; the stops also come with the same number of days before, for the change). Passes a person called false alarms are in no share and are counted apart; an hour or a kind is compared only when at least 5 of its vehicles could be judged. A speed in words ("about 4 mph") takes a vehicle's size (the width and the height of its box in the picture, averaged) to be a car's, about 2.5 m (it first took the size for a car's length, 4.5 m, which made every speed about 1.8 times too high; plan.md D41). The Data page also gives the fastest estimated speed of the period and of each day (D41). Without weeks of traffic the page is thin; to see it full, fill a scratch data directory with made-up passes (`Event` rows with `type="stop"` and `MinuteCount` rows, through `open_store`), give it a password with `trafficpi-admin --data-dir ... set-password` and run `trafficpi-api --data-dir ... --port 19000`.
- **A build is live at once.** The running API reads `dashboard/out` for every request (the hashes in each page's Content-Security-Policy follow the files, `backend/trafficpi/api/static.py`), so `pnpm build` in the checkout deploys whatever the working tree holds, work in progress included, with no restart. To look at a build first, copy `dashboard/` somewhere on the same disk (`git archive <commit or tree> dashboard | tar -x -C <dir>` for what is committed, and add `backend/tests/data` to it, which three unit tests read; then `cp -al node_modules` to share the packages), build the copy there (a heavy job: see "Heavy jobs share the Pi with the camera" below), and start the made-up street's API with `--dashboard-dir <copy>/out` on other ports. To put the result live, copy its `out/` over `dashboard/out` and keep the old one for a way back.
- **Stop-sign hours** (plan.md D42). The Setup page's Stop-sign hours card limits when stop signs are judged: in daylight (the camera's latitude and longitude, from some minutes before sunrise to some minutes after sunset) and/or at set times (ranges of the day on the camera's clock, on chosen days); outside the hours a pass is not judged at all. The sun is worked out in `backend/trafficpi/core/sun.py` (NOAA's formulas, whole minutes, by solar day) and checked against `backend/tests/data/sun_cases.json`: 24 places and days taken word for word from the U.S. Naval Observatory's service (one is fetched with `https://aa.usno.navy.mil/api/rstt/oneday?date=2026-10-09&coords=40.7128,-74.0060&tz=-4&dst=false`; add a case by keeping what it answers). The rule is `core/stop_hours.py`; the vision service applies it (`VisionService._judging_stops`) and the API serves it (`GET` and `PUT /api/stops/hours`, and `POST /api/stops/hours/check`, which says what a setting that is not saved would mean, for the card's preview). To see it work on the made-up street, save a range that does not hold the present: the vision service stops storing passes (`stops_skipped` in its `/health`, `stop-sign judging is paused` in its log) and starts again when the limit is switched off; the browser test does exactly that.
- **Hot reload.** `pnpm dev` serves the dashboard on port 3000 and forwards `/api`, the live socket included, to an API on port 8000 (set `TRAFFICPI_API` for another). The API refuses requests that come from another origin, so start it with `uv run trafficpi-api --allow-origin http://127.0.0.1:3000`. That option is for development only: a listed origin can act as a logged-in user.
- **Browser tests.** `sudo apt install chromium-headless-shell`, start a stack (the made-up street above is enough, started with `--history-days 3` or more; `E2E_PASSWORD=... scripts/start_demo.sh DATA_DIR LOG_DIR [API_PORT [VISION_PORT]]` starts it with the API on a fresh data directory and waits until both answer, and is what CI runs), then `E2E_PASSWORD=... E2E_DEMO=1 node e2e/smoke.mjs` from `dashboard/` (`E2E_BASE_URL` for a stack on other ports). It signs in, goes to the Setup page, clears the lines and zones, then sets counting up with the mouse and keyboard: adds a line, drags its ends into place (and checks the stored coordinates), names it and saves; waits for vehicles to be counted; adds a detection zone, drags a corner, adds one by its edge dot and removes it by pressing twice; undoes and redoes a drag (with the keyboard focus elsewhere: undo works anywhere on the page, except in a text field); tries a crossing outline (refused, with its reason) and arrow-key nudges; adds an exclusion zone and sees the live view draw the zones; keeps the picture, exports and imports the profile (and is refused a file that is not one); reloads; shows the live video full screen, by the overlay used where the browser has no full screen for a picture (an iPhone's Safari) and by the browser's own, and leaves again; hovers the chart, switches to the table, uses the Trails card (the paths of a day over the camera picture; hours picked by click, by drag, with Shift and with the keyboard, and the count that follows; each kind switched off; the heat map, which is remembered; the day by the arrows and by date, and a day with nothing; playing the day from the hours picked; saving the picture and checking the PNG; the lines and zones over it; and, with the API's answers faked, no trails at all, a failed request with Try again, and the day of 25 hours with its hour that happens twice), changes and saves the settings (a time zone that does not exist is refused, the room for pictures and clips is checked, and an unsaved change is asked about before leaving), opens the (i) icons beside the options (by hovering, from the keyboard, by a press and by a tap, one at a time and always inside the window, the words read from the bubbles), sets the stop-sign hours (the daylight limit with a longitude of the wrong sign, which the card warns about, then the right one, the sun's times and the window shown as they are typed and the same as the server's, the margins, a save and a reload, the line on the Stop signs card and on the Data page, set times as ranges through midnight on weekdays only, a range that starts and ends together refused, both limits told in one sentence, and the made-up street's vehicles no longer judged at the stop sign while the hours do not hold the present, and judged again when the limit is off), adds a stop sign on the made-up street's near lane (dragging its zone's corners and the ends of its line, then the live view's red stop line and the vehicles being judged at it, the warning for a zone on the wrong side, the numbers that count as a stop, and the kinds that must stop), uses the Stop signs card (the passes that need review, a pass called a false alarm and found again under False alarms after a reload and taken back, one confirmed, the period kept, the picture and the clip kept of a pass that needs review (the picture loads, the clip is an MP4 of 640 by 360 that the browser reads and a part of it can be asked for, it opens in a window where a review is made and taken back, Newer and Older go through the passes, Escape gives the focus back), and, with the API's answers faked, the message when no stop sign is set up), and signs out. Set `CHROMIUM` to use another browser and `E2E_SHOTS=<dir>` to keep screenshots. It deletes and rewrites the lines and zones of the database it runs against, so never point it at a stack that counts real traffic. `node e2e/screenshots.mjs <dir>` photographs the dashboard, with the editor open, in light and dark, on a desktop and on a phone (it needs some lines and zones to exist: run the browser test first; `E2E_TRAILS_DAY=2026-10-08` picks the day shown on the History page). CI runs the browser test against the demo street.
- **The API's types.** The TypeScript types are generated from the API's OpenAPI schema and committed. After changing a route or a response model, run `scripts/gen_api_types.sh` and commit `dashboard/src/lib/openapi.json` and `api-types.ts`; CI fails when they are out of date.
- **Explaining an option.** Not with a paragraph under it. `components/ui/info-tip.tsx` is a small (i) whose bubble shows the text while the mouse is over it or the keyboard is on it, and after a press (the only way a finger has); leaving, Escape, a press elsewhere or opening another one shuts it, and the pointer can rest on the bubble. `Switch`, `Label`, `NumberField` and `OptionGroup` (a titled set) take a `hint` that puts one beside the label and names its text in `aria-describedby`; the text is always in the page, hidden while the bubble is shut. What goes in a hint: what the option does and the echo of its value ("Kept for 30 days."). A hint says what the option does and never why anyone would use it: no reason to switch it on, no situation it suits (a time of day, a kind of street, a day it went wrong), because a reason belongs to one installation and an option to every installation. The paragraph that opens a card and the guides follow the same rule. A warning about what an option costs or risks (a third party's terms, data that cannot be recovered) is not a reason, and stays. What stays visible beside the control: what is wrong with the value (in the critical colour), why a control is greyed out, and actions. The button is named "More information" and nothing more, so that `getByLabel("Time zone")` still finds only the field. Where the bubble goes is a pure function (`src/lib/tip.ts`: under the icon, over it when there is no room, never within 8 px of the window's edge). In the browser test read a bubble with `explained(control)` or `explainedBy(icon)`: it is hidden until hovered, so `getByText(...).waitFor()` on it times out. No library does this (no Radix, no Floating UI): about a hundred lines, and nothing more for `scripts/audit_deps.sh` to check.
- **Colours.** The tokens in `src/app/globals.css` come from a validated data-viz palette (light and dark); a vehicle class always has the same colour. `src/lib/tokens.test.ts` checks every text colour pair against WCAG AA, so check that test before changing a colour.
- **How the pages look and read** (plan.md D50 to D56). A date or a time is written by `src/lib/format.ts` and nothing else: `dayText` ("Fri, Oct 9"), `timeText` ("22:07"), `momentText` and `atText` for something recent ("2 min ago", "yesterday at 22:07"), `fullTimeText` for a tooltip; times are 24-hour everywhere, seconds only on the live feed, and the time zone is said once, as the chip in the header, never in a card. A time is typed in `TimeField` (a 24-hour text field), not an `<input type="time">`, which shows "07:00 AM" to a browser in English. The words for an outcome and a review state are in `src/lib/stops.ts` (`OUTCOME_LABEL`, `REVIEW_LABEL`) and a page does not write its own. A card title is `CardTitle` (16 px), a heading above cards `text-xl`, text `text-sm`, nothing is under 12 px, and a figure is one of the three `METRIC_SIZE` tiers. A thing that is chosen looks one way, the accent wash with an accent edge and a check (`Segmented`, `ToggleChip`, `CheckChip`, the tiles); solid accent blue is a form's one primary button, which is neutral grey while there is nothing to save. A card opens with one sentence (`CardSubtitle`) and the rest sits behind `HowItWorks`. Neither a vehicle's kind nor an outcome is told by colour alone (`ClassIcon`, `OutcomeIcon`). A page's wrapper is `grid grid-cols-1`, and a control that can be wide is `min-w-0 max-w-full` or scrolls inside its own box: without that a phone scrolls sideways, which the browser test checks at 390 px. Under a coarse pointer (`pointer-coarse:`) a target is 40 px.
- **TypeScript stays at 5.9**, because typescript-eslint does not support 7 yet. pnpm 12 does not run install scripts unless `pnpm-workspace.yaml` allows them.
- If a build crashes in an odd way, look at the memory first (see Troubleshooting) and delete `.next` before trying again; also check that no orphaned `next` worker is still using the CPU (`ps -eo pid,pcpu,args | grep next`).

## Heavy jobs share the Pi with the camera

The Pi that counts traffic is also the development machine. The camera and the accelerator alone keep it at 72 to 75 °C; a backend suite, a type check or a dashboard build takes it to 84 °C, and from 80 °C it slows itself down (`vcgencmd get_throttled` shows `0xe0000` once it has). The camera loop kept 30 frames a second through that every time it was looked at. But on 2026-10-09 the camera stalled while an un-niced type check and a test run were going, the vision watchdog restarted the service, and about four minutes later the whole board froze and was reset by its hardware watchdog; the cause was not found (plan.md R19). The load is the one thing we control, so:

```
taskset -c 2,3 nice -n 15 flock /tmp/trafficpi-heavy.lock <command>
```

runs a heavy job (`pytest` of more than a few files, `pyright`, `pnpm build`, `tsc`, `scripts/rehearse_ci.sh`, a stress run) on two of the four cores, at the lowest priority, and only when no other heavy job is running (the lock). `scripts/test_in_slices.sh` runs the whole backend suite that way, eight test files at a time, and waits between them for the board to drop below 78 °C. Look at `vcgencmd measure_temp` before a long run. Single-test runs need none of this.

The same link carries everything else: the Pi's Wi-Fi was measured at -72 dBm with a receive rate of 1 Mbit/s that evening, and one live viewer (about 2 Mbit/s) was enough to leave it unable to fetch packages (plan.md R20). If a download times out, look at `iw dev wlan0 link` before suspecting the code; the rehearsal can run from the package caches with `UV_OFFLINE=1 npm_config_offline=true scripts/rehearse_ci.sh`.

## Working in a second checkout (a git worktree)

When two sessions work at once, one of them can take `git worktree add ../TrafficPi-<name> -b <branch>` and leave `/home/jet/TrafficPi` on `main` for the other. Three things differ in the second checkout:

- `uv run` inside its `backend/` builds a second virtualenv without the Pi's system packages (the camera libraries) and wants the network. Run the tests with the main checkout's virtualenv instead, from the worktree's `backend/`: `PYTHONPATH=. /home/jet/TrafficPi/backend/.venv/bin/python -m pytest tests/test_x.py`, and `-m trafficpi.api.openapi ../dashboard/src/lib/openapi.json` for the generated API types (`scripts/gen_api_types.sh` uses `uv run`; the TypeScript half, `pnpm exec openapi-typescript`, works as it is). To try the dashboard against a made-up street, start the services with `PYTHONPATH=<the worktree's backend>` from the main checkout's `backend/` and point `--dashboard-dir` at the worktree's `dashboard/out`.
- `pyright` takes its virtualenv from `backend/.venv` (`pyproject.toml`), so give the worktree a symlink to the main one for the run and take it away afterwards (`rm backend/.venv`; git does not ignore a symlink). Never run `uv run` while the symlink is there: it would sync the main checkout's virtualenv.
- The two checkouts share the repository but not the files, so the notes (plan.md, todo.md, owner.md) are edited in whichever checkout the work is in and merge like code. Merge into `main` only when the other session has nothing uncommitted in the shared checkout that the merge would overwrite (`git merge --ff-only` refuses, and says which file).

## Rehearsing CI

`scripts/rehearse_ci.sh` clones what is committed to a temporary directory and runs CI's four jobs there (backend: lint, types, tests in a virtualenv without the Pi's system packages; dashboard: install from the lockfile, lint, types before any build, tests, build; the check that the generated API types are current; the browser test against the made-up street on ports 18000 and 18001, beside the services). It takes about eight minutes and ends with the number of failed jobs as its exit status. Run it after committing and before pushing: this Pi has things a runner does not (Picamera2, HailoRT, build output, files that `.gitignore` hides), and the first push failed twice for that reason. A third time the rehearsal had its own copy of how CI starts the services, and waited for both where CI waited for one: the vision service listens a few seconds after the API, so CI asked it too early and failed. Both now start them with `scripts/start_demo.sh`, so change that script, not one of the two callers. The rest can still differ: CI uses Google Chrome and the rehearsal the old headless shell by default, so `CHROMIUM=/path/to/a/full/chromium scripts/rehearse_ci.sh` is nearer (Playwright's own build, if it was downloaded, is in `~/.cache/ms-playwright/chromium-*/`). `--skip-e2e` leaves out the browser test.

## Troubleshooting

- **`hailortcli` can't find a device.** Check `lsmod | grep hailo`, `dkms status` and `sudo dmesg | grep -i hailo`. After a kernel update the driver must be rebuilt for the new kernel: `sudo dkms autoinstall` (kernel headers must be installed).
- **Camera busy.** libcamera lets only one process open a camera. Stop any other `rpicam-*` or Picamera2 process first.
- **Reflections in the image.** If the lens sits behind a window, indoor lights and bright surfaces show up as ghost overlays, worst at dusk and night. Put the lens against the glass with a light shield, or mount it outside.
- **A Python process using the accelerator segfaults a moment after it "closes" it.** HailoRT 4.23.0 crashes after teardown on this platform: releasing the virtual device crashed 15 of 15 runs and dropping a configured model 5 of 15, while never closing crashed none. `HailoDetector.close()` therefore keeps the objects alive until the process exits. Do not call `Hailo.close()` yourself, and restart the process to change models.
- **`trafficpi-vision` ends with exit code 139 or 135 (SIGSEGV, SIGBUS) right after logging that it stopped.** HailoRT 4.23.0 also crashes inside its own exit code, seen when the live-view server started serving after the model was configured. The `trafficpi-vision` command therefore leaves with `os._exit` after closing everything of its own. If you start the code some other way (a script of your own that imports the detector), end it with `os._exit(0)` after flushing.
- **The service says "cannot start the camera ... is another program using it?"** Another process holds the camera, often a forgotten `rpicam-hello` or `trafficpi-vision record`. It is retried every 5 s and taken over a few seconds after the other program exits.
- **`hailort.log` appears in the working directory.** HailoRT writes one line per inference there. Set `HAILORT_LOGGER_PATH=NONE` (the backend does this itself) or point it at a directory.
- **`dmesg` shows `WARNING ... find_vma` from `hailo_vdma_buffer_map`.** A locking bug in the Hailo PCIe driver 4.23.0 on kernel 6.18 (it calls `find_vma` without `mmap_lock`). It comes in a burst of 42 at every start of the vision service (and was seen once in a crashing run), and has done no harm so far. One burst is most of the kernel log buffer, so `dmesg` keeps only the latest; `journalctl -k` keeps them all.
- **Random crashes, results that differ between identical runs, garbled strings (segfaults in `node`, `tsc`, `next build`; a password that sometimes fails to verify).** Suspect the memory before the software. This board has a defective RAM page (physical `0x1dfbb0000`, plan.md R16). Test with `sudo memtester 1024M 1` (look for `FAILURE`) and locate bad pages with `sudo scripts/find_bad_memory.py --gib 4` (add `--hold &` to keep them out of use until the next reboot). It tests only memory that is free at the time, so it cannot see a page the overlay below already reserves; for that, read `/proc/iomem` as root. The permanent fixes are a replacement board, or reserving the page at boot with a device-tree overlay. **The overlay below has been in effect since the reboot of 2026-10-09 00:47**: its source is `/boot/firmware/trafficpi-badram.dts`, built with `sudo dtc -@ -I dts -O dtb -o /boot/firmware/overlays/trafficpi-badram.dtbo /boot/firmware/trafficpi-badram.dts`, and `dtoverlay=trafficpi-badram` is the last line of `/boot/firmware/config.txt` (under `[all]`; the file as it was is `config.txt.bak-before-badram`). Before a reboot, check that it merges onto the base trees with the firmware's own tool: `dtmerge /boot/firmware/bcm2712-rpi-5-b.dtb /tmp/merged.dtb /boot/firmware/overlays/trafficpi-badram.dtbo`, then `dtc -I dtb -O dts /tmp/merged.dtb | grep -A3 badram@` (do the same for `bcm2712-d-rpi-5-b.dtb` and `bcm2712d0-rpi-5-b.dtb`). After the reboot, `sudo grep -i 1dfbb /proc/iomem` should show `1dfbb0000-1dfbb3fff : reserved` (as root; other users see no addresses), `journalctl -k -b | grep badram` the kernel's `OF: reserved mem: ... nomap non-reusable badram@1dfbb0000`, and `sudo scripts/find_bad_memory.py` (without `--hold`) should find no bad page. That is how it was checked on 2026-10-09; `scripts/check_boot.sh` now checks the first. To undo it, delete that last line of `config.txt` (from another computer if the Pi does not boot) or put the backup back. The address belongs to this board; another board needs its own (run the finder).

  ```dts
  /dts-v1/;
  /plugin/;
  / {
      compatible = "brcm,bcm2712";
      fragment@0 {
          target-path = "/reserved-memory";
          __overlay__ {
              #address-cells = <2>;
              #size-cells = <2>;
              badram@1dfbb0000 { reg = <0x1 0xdfbb0000 0x0 0x4000>; no-map; };
          };
      };
  };
  ```

  Adopt a new Node major version only after a loop of 40 `npm config get cache` runs *and* a clean memory test.
- **Pyright cannot find `picamera2` or `libcamera`.** It does not search Debian's `dist-packages`. The project ships stubs for the parts it uses in `backend/typings/`; extend them when adding new calls.
