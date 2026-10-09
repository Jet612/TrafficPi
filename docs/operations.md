# Running TrafficPi

What to look at, what to do when it needs attention, and how the pieces look after themselves. It assumes TrafficPi was installed with [install.md](install.md) (or by hand as in [dev-setup.md](dev-setup.md)). Commands are run from the checkout (`~/TrafficPi`) unless they say otherwise.

## At a glance

| What | Where |
|---|---|
| The dashboard and its API | `trafficpi-api.service`, port 8000 on every address of the Pi |
| The camera, the accelerator, the counting | `trafficpi-vision.service`, port 8001 on `127.0.0.1` only (the API is its public door) |
| The database, pictures, clips, session key | `backend/data` (`$TRAFFICPI_DATA_DIR` if you set one), readable by your user only |
| The log | the journal: `journalctl -u trafficpi-vision -u trafficpi-api` (kept across reboots, 256 MB or a month) |
| The nightly backup, if you chose one | `trafficpi-backup.timer`, archives in `backend/data/backups` |
| The daily reboot, if you chose one | `trafficpi-reboot.timer` |

## Looking at it

- **The dashboard's System page** shows the camera and the frame rate, how much of the processor and memory is used, the temperature and the Pi's throttle flags in words, the accelerator (chip, firmware, model, how long a picture takes), how long each service has been up, what the data directory holds, the last backup and the installer's memory test, the disk, and (when the camera has moved from where it was when you saved the reference picture) a warning.
- **"Health over time"**, on the same page, is the record behind those numbers: the vision service stores a reading of the machine every minute for 30 days (temperature, load, frame rate, the Pi's flags, whether the camera worked), and the card draws the last hour, 6 hours, a day, a week or a month, with the numbers over it and a list of what happened: **the Pi slowing itself down** (with how hot it got), **a weak power supply**, **the camera not working**, and **nothing recorded** (the service was off or restarting for more than three minutes). A gap in a line is a time when nothing was recorded or the camera did not work. "Table" shows the same numbers as text. This is where to look when the board "was fine yesterday" and is not today, and after a restart that nobody asked for.
- **"What the camera looks for"**, in Settings on the Setup page: vehicles are always looked for; "Look for people" can be switched off, and then nobody who walks past gets a box in the picture, a path or a count. It applies at once.
- **`systemctl status trafficpi-vision trafficpi-api`** says whether each is running, for how long, and what the vision service is doing right now on its `Status:` line, for example `counting at 30.0 fps with yolov8s_h8l`, or `camera offline: ...`.
- **`scripts/check_boot.sh`** runs eleven checks of the accelerator, the driver, the camera, the link and the services, and says PASS or FAIL for each. Run it after a reboot or an update.
- **`journalctl -u trafficpi-vision -b`** is how this boot began; **`-b -1`** is the boot before it (how the last reboot went down).

## Restarting, and what it costs

`sudo systemctl restart trafficpi-api` costs nothing you will notice: sessions are signed cookies, so nobody is signed out. `sudo systemctl restart trafficpi-vision` stops the counting for about ten seconds (the camera and the accelerator have to be opened again), and a vehicle that crosses a line in that time is not counted. Stopping the vision service does not take the dashboard down: it says "camera offline" until the service is back.

Only one program can open the camera and the accelerator. Before you run `trafficpi-vision snapshot`, `record` or `dry-run` by hand: `sudo systemctl stop trafficpi-vision`, and start it again afterwards.

## When something hangs

Two things bring a hung service back by themselves:

- **systemd's watchdog.** Both services tell systemd every few seconds that they are alive. The vision service says so only while its camera loop is moving (it is *not* moving if a call into the camera, the accelerator or the database never returns); the API says so only while it can still hand a request to a worker thread. If the messages stop for a minute (vision) or 45 seconds (API), systemd kills the service and starts it again. The journal then shows `Watchdog timeout` from systemd and, just before it, a line from `trafficpi.watchdog`: *"the work loop has stopped moving ... It is stuck here:"* followed by the stack of the stuck thread. **That stack is what to send with a bug report.**
- **The Pi's own hardware watchdog.** Raspberry Pi OS feeds it from systemd; if the kernel or systemd itself hangs for a minute the board resets.

A camera that stops delivering frames is dealt with inside the service, which reports it on the dashboard and tries again every 5 seconds; after five minutes without a camera it exits, and systemd starts it afresh.

## The nightly reboot and the journal

If you installed with `--reboot-at 03:00` the Pi reboots at that local time every day (unless it came up less than ten minutes before). Nothing is counted for about a minute. Because the journal is kept on the disk, `journalctl -b -1` shows how the last boot ended. Turn it off with `scripts/install_services.sh --reboot-off`.

## Backups

A backup is one file, `trafficpi-backup-YYYYMMDD-HHMMSS.tar.gz`, holding a consistent copy of the database (taken while the services run), the session key, the reference picture and, if you ask, the pictures and clips. **It holds your password hash and the key that signs sessions: keep it private.**

```
backend/.venv/bin/trafficpi-admin backup                     # into backend/data/backups
backend/.venv/bin/trafficpi-admin backup --config-only       # leave out the history: settings, profile, users, key
backend/.venv/bin/trafficpi-admin backup --with-evidence     # also the pictures and clips (they can be large)
backend/.venv/bin/trafficpi-admin backup --to /media/usb/trafficpi --keep 14
```

A nightly backup is one option of the installer: `scripts/install_services.sh --enable --backup-at 03:30` (add `--backup-to /media/usb/trafficpi` for another place and `--backup-keep 14` for more than the default 7; `--backup-off` takes it out). It runs at the lowest priority.

**A backup on the Pi's own card does not survive the card.** It guards against a wrong click, a bad migration and a deleted folder. Copy the files somewhere else too, from another computer:

```
rsync -av pi@raspberrypi.local:TrafficPi/backend/data/backups/ ~/trafficpi-backups/
```

### Restoring

```
sudo systemctl stop trafficpi-vision trafficpi-api
backend/.venv/bin/trafficpi-admin restore /path/to/trafficpi-backup-20261009-033000.tar.gz
sudo systemctl start trafficpi-vision trafficpi-api
```

It checks the archive and the database in it before it changes anything, **refuses while a program has the database open** (it says which), and sets the files it replaces aside in `backend/data/before-restore-<time>` instead of deleting them (delete that folder when you are content). A backup made by a newer TrafficPi is refused; one made by an older one is brought up to date. A `--config-only` backup restores the settings, the profile, the users and the key, and leaves you without history: what was there before goes to `before-restore-…`.

### To a new card or a new Pi

Install on the new one with [install.md](install.md), stop the services, `trafficpi-admin restore` the newest backup, start them. The password, the sessions, the zones and the history come with it. (The pictures and clips only if the backup was made with `--with-evidence`.) Draw nothing again, but check the camera's view against the reference picture: a moved camera is told on every page.

## Updating

```
scripts/update.sh --pull
```

fetches the newest version (it refuses if you have changed files in the checkout), installs the locked Python packages, builds the dashboard if it changed, puts the systemd units in again (with the daily reboot, the nightly backup, the journal and the proxy setting if you had them), restarts the services, waits for them and runs `scripts/check_boot.sh`. The database is brought up to date by the services as they start. `--dry-run` says what it would do; `--no-restart` leaves the old services running.

After an `apt full-upgrade` that installs a **new kernel**, the Hailo driver is rebuilt for it (DKMS) the next time the Pi boots, which is a reboot (or the 03:00 one). The morning after, `scripts/check_boot.sh` tells you whether the driver is loaded. The four Hailo packages are held so that an upgrade cannot change them one by one; to upgrade them together: `sudo apt-mark unhold hailort hailort-pcie-driver python3-hailort hailo-models`, upgrade, test, hold again.

To go back to an earlier version: `git checkout <the earlier commit>` and `scripts/update.sh`.

## The disk

| What | How it grows | What limits it |
|---|---|---|
| Trails (the paths of vehicles) | about 100 bytes a vehicle: 1 to 3 MB a day on a busy street | deleted after 365 days (Settings) |
| Events and minute counts | a few KB a day | kept |
| Pictures and clips of stop-sign passes | 35 KB a picture, about 50 KB a clip | 30 days or 2 GB, and clips only 3 hours or 2 days (Settings) |
| The journal | a few MB a day | 256 MB or a month |
| Backups | one archive a night | the newest 7 |

The System page shows the free space. A card that is nearly full makes everything slow, and SQLite fails when it cannot write.

## Heat and power

A Pi 5 with the Active Cooler settled at 72 to 74 °C running only TrafficPi here, and reached 84 °C when builds and test runs shared it (the board then slows itself down; the camera still held 30 frames a second, because the vision service has a larger share of the processor, but anything else slows). The System page shows the temperature and the Pi's flags in words; `vcgencmd get_throttled` shows them raw (`0x0` is clean; bits 16 to 19 say that something *has* happened since boot). In an enclosure outdoors, give it air and shade, and watch the System page on the first hot afternoon. A power supply that cannot deliver 5 A shows up as "under-voltage".

## The memory test

A defective RAM page on the development board showed up only as random, hard-to-explain failures (plan.md R16). The installer runs `memtester` over 512 MB once, which can miss one bad page in 8 GB; `sudo scripts/find_bad_memory.py` scans all the free memory in chunks and names a bad page's address, and `sudo memtester 1G 1` is the quick look. A board that fails is a board whose other results nobody can trust.

## When something is wrong

| What you see | What to do |
|---|---|
| The dashboard does not load | `systemctl status trafficpi-api`; `curl http://localhost:8000/api/health` on the Pi; `journalctl -u trafficpi-api -n 50`. |
| "Camera offline" | `systemctl status trafficpi-vision` and its `Status:` line; `rpicam-hello --list-cameras` (with the service stopped) must show `imx708`; reseat the ribbon cable; `scripts/check_boot.sh`. |
| Counting stopped, the camera says "ok" | `journalctl -u trafficpi-vision -n 100`; a profile with no line counts nothing (the log says so). |
| `check_boot.sh`: `hailo_pci` not loaded | A new kernel is running without its driver: `sudo dkms autoinstall && sudo modprobe hailo_pci`, then reboot if that fails. |
| `check_boot.sh`: PCIe link is Gen 2 | Add `dtparam=pciex1_gen=3` to `/boot/firmware/config.txt` and reboot. |
| "Too many failed logins" | Eight failures in ten minutes lock an address or a name until the ten minutes pass; restarting `trafficpi-api` clears it. The journal says whose. |
| Forgot the password | `backend/.venv/bin/trafficpi-admin set-password` (it ends every session). |
| Disk full | The System page; `du -sh backend/data/*`; lower the days or megabytes in Settings; delete old `backups` and `before-restore-*` folders. |
| The dashboard is slow, the live video stutters or the page keeps reconnecting | Look at the Wi-Fi: `iw dev wlan0 link` (a signal worse than about -70 dBm, or a receive rate of a few Mbit/s, is weak) and `ping` the router. One live viewer takes about 2 Mbit/s of the Pi's side, so a weak link fills up and everything else on it waits. A cable to the router (the Pi's Ethernet port) is the cure; `sudo iw dev wlan0 set power_save off` helps a little and lasts until the next boot. |
| A page is blank after a Cloudflare change | [remote-access.md](remote-access.md), step 6. |
| The Pi restarted and nobody asked it to | `journalctl -b -1 -n 100`: a restart that was asked for ends with systemd stopping the units; a log that just stops is a freeze or a power cut, and the Pi's hardware watchdog then resets the board about a minute later. `vcgencmd get_rsts` read `20` after such a reset on the development board (compare it with what a normal reboot reads on yours). The System page's history shows the temperature, the Pi's own flags and the frame rate for the minutes before. Then: a different 27 W supply, the cooler and the ribbon cable, and keep other heavy work off the Pi while it counts. A second one without a cause is a reason to suspect the board (plan.md R19). |
| The camera view moved | The notice on every page says so; put it back, or draw the zones again on a new reference picture (Setup). |
