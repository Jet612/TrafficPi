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

After a reboot, `scripts/check_boot.sh` runs the whole set of checks below (module, driver, device node, accelerator, bootloader, camera, PCIe link and a 10 s smoke test) and prints PASS or FAIL for each.

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

# Node.js 22 LTS ("Jod"). Debian's own nodejs is v20 (past upstream end-of-life), and Node 24.21.0
# crashed intermittently on this machine (see Troubleshooting), so the major version is pinned.
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

Runtime files (snapshots, clips, detection logs) go under `$TRAFFICPI_DATA_DIR`, by default `./data`, which is git-ignored. Recordings show whatever the camera sees, so keep them out of the repository.

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

## Troubleshooting

- **`hailortcli` can't find a device.** Check `lsmod | grep hailo`, `dkms status` and `sudo dmesg | grep -i hailo`. After a kernel update the driver must be rebuilt for the new kernel: `sudo dkms autoinstall` (kernel headers must be installed).
- **Camera busy.** libcamera lets only one process open a camera. Stop any other `rpicam-*` or Picamera2 process first.
- **Reflections in the image.** If the lens sits behind a window, indoor lights and bright surfaces show up as ghost overlays, worst at dusk and night. Put the lens against the glass with a light shield, or mount it outside.
- **A Python process using the accelerator segfaults a moment after it "closes" it.** HailoRT 4.23.0 crashes after teardown on this platform: releasing the virtual device crashed 15 of 15 runs and dropping a configured model 5 of 15, while never closing crashed none. `HailoDetector.close()` therefore keeps the objects alive until the process exits. Do not call `Hailo.close()` yourself, and restart the process to change models.
- **`hailort.log` appears in the working directory.** HailoRT writes one line per inference there. Set `HAILORT_LOGGER_PATH=NONE` (the backend does this itself) or point it at a directory.
- **`dmesg` shows `WARNING ... find_vma` from `hailo_vdma_buffer_map`.** A locking bug in the Hailo PCIe driver 4.23.0 on kernel 6.18 (it calls `find_vma` without `mmap_lock`). Seen once, in 42 lines, during a crashing run.
- **`node` or `npm` crashes (segfault, `free(): invalid next size`, illegal instruction).** Node 24.21.0 does this on this machine (about 1 in 5 `npm` runs); Node 20, 22 and 26 did not. Use Node 22 and check any new version with a loop of 40 `npm config get cache` runs before adopting it.
- **Pyright cannot find `picamera2` or `libcamera`.** It does not search Debian's `dist-packages`. The project ships stubs for the parts it uses in `backend/typings/`; extend them when adding new calls.
