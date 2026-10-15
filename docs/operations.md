# Running TrafficPi

What to look at, what to do when it needs attention, and how the pieces look after themselves. It assumes TrafficPi was installed with [install.md](install.md) (or by hand as in [dev-setup.md](dev-setup.md)). Commands are run from the checkout (`~/TrafficPi`) unless they say otherwise.

## At a glance

| What | Where |
|---|---|
| The dashboard and its API | `trafficpi-api.service`, port 8000 on every address of the Pi |
| The camera, the accelerator, the counting | `trafficpi-vision.service`, port 8001 on `127.0.0.1` only (the API is its public door) |
| The database, pictures, clips, session key, the email provider's key | `backend/data` (`$TRAFFICPI_DATA_DIR` if you set one), readable by your user only |
| The log | the journal: `journalctl -u trafficpi-vision -u trafficpi-api` (kept across reboots, 256 MB or a month) |
| The nightly backup, if you chose one | `trafficpi-backup.timer`, archives in `backend/data/backups` |
| The daily reboot, if you chose one | `trafficpi-reboot.timer` |

## Looking at it

- **The dashboard's System page** shows the camera and the frame rate, how much of the processor and memory is used, the temperature and the Pi's throttle flags in words, the accelerator (chip, firmware, model, how long a picture takes), how long each service has been up, what the data directory holds, the last backup and the installer's memory test, the disk, and (when the camera has moved from where it was when you saved the reference picture) a warning.
- **"Health over time"**, on the same page, is the record behind those numbers: the vision service stores a reading of the machine every minute for 30 days (temperature, load, frame rate, the Pi's flags, whether the camera worked), and the card draws the last hour, 6 hours, a day, a week or a month, with the numbers over it and a list of what happened: **the Pi slowing itself down** (with how hot it got), **a weak power supply**, **the camera not working**, and **nothing recorded** (the service was off or restarting for more than three minutes). A gap in a line is a time when nothing was recorded or the camera did not work. "Table" shows the same numbers as text. This is where to look when the board "was fine yesterday" and is not today, and after a restart that nobody asked for.
- **"What the camera looks for"**, in Settings on the Setup page: vehicles are always looked for; "Look for people" can be switched off, and then nobody who walks past gets a box in the picture, a path or a count. It applies at once.
- **`systemctl status trafficpi-vision trafficpi-api`** says whether each is running, for how long, and what the vision service is doing right now on its `Status:` line, for example `counting at 30.0 fps with yolov8s_h8l`, or `camera offline: ...`.
- **`scripts/check_boot.sh`** runs eleven checks of the accelerator, the driver, the camera, the link and the services, and says PASS or FAIL for each. Run it after a reboot or an update.
- **`scripts/hardware_check.sh`** stops the vision service and runs what needs the camera and the accelerator to itself: a memory test (`--memtest`), the tests that need the hardware (`--hardware-tests`), a baseline of the camera and the model (`--baseline`, to compare with the figures in [accuracy.md](accuracy.md)) and, given a recorded clip (`--clip`), the clip through the three models, compared and replayed through the clip's profile. It starts the service again whatever happens, and the Pi counts nothing while it runs, so run it at a quiet hour. After an update of HailoRT, the driver or the kernel is a good time.
- **A disk that is nearly full.** The database is on the same card as the pictures and clips, so while less than 1 GB is free no picture or clip is written, and the oldest are deleted (every ten minutes) until there is room again; the counting goes on. The journal says it (`the disk has N MB free, under the 1000 MB that is kept free`, at most once an hour), and `curl -s http://127.0.0.1:8001/health` has `disk_free_mb` and `evidence_skipped_for_space` (how many passes since the service started got no evidence for that reason; 0 when all is well). The System page shows the disk in use. Counts, passes and trails are small and are not deleted for room: this street's database grew about 1 MB a day.
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

## Stop-sign hours: when stop signs are judged

The hours in which stop signs are judged can be limited: Setup page, **Stop-sign hours**. There are two limits, each with a switch of its own, and a pass is judged only if it is inside every limit that is on. With both off, every pass is judged at every hour.

- **In daylight.** From some minutes before sunrise to some minutes after sunset where the camera is: 30 and 30 to begin with, from -120 to 180 each (a negative number starts later, or stops sooner, than the sun). It needs the camera's **latitude and longitude** in decimal degrees: north and east are positive, so a place in the Americas has a **negative longitude** (New York is 40.71 and -74.01). Look them up in a maps app (press on the spot and copy the two numbers); two decimals, about a kilometre, is plenty. Sunrise and sunset are worked out on the Pi from NOAA's formulas, with no network and no account; they agree with the U.S. Naval Observatory's published times to within a minute (the tests compare 24 places and days, the midnight sun and the polar night included). The card shows the sun's times for today by the camera's clock **as you type**, and warns when the sun would be highest hours away from midday on that clock, which is nearly always a longitude with the wrong sign.
- **At set times.** One to six ranges of the day by the camera's clock (the time zone in Settings), on the days of the week you tick. A range may run through midnight (22:00 to 06:00), and belongs to the day it starts on; "everything except 16:00 to 18:00" is the range 18:00 to 16:00.

**What an hour outside the limits means.** A vehicle that crosses a stop line then is **not judged at all**: no row in the history, no picture, no clip, nothing on the Data page or in the daily email (which says so in a line, so that a quiet stretch is not read as a safe one). It cannot be recovered afterwards. Everything else goes on: vehicles are counted at the counting lines, their paths are saved, the live picture is drawn. Passes that were judged before a limit was switched on stay where they are; nothing is deleted. They stay in the Data page's numbers too, and its Stop-sign results card says how many of the passes it counts fall outside the hours as they are now ("Includes 12 passes recorded outside today's judging hours.").

**Where to look.** The card says whether stop signs are being judged now and when that next changes; the Stop signs card on the Review page and the Data page carry a line about the hours while a limit is on. The journal says it as it happens (`journalctl -u trafficpi-vision | grep -i "stop-sign judging"`): `stop-sign judging is paused: outside the hours set for it; it resumes Sat 06:32`, then `stop-sign judging has resumed`, and at each start which hours are set. The vision service's `/health` has `stops_skipped`, the number of passes the hours turned away since it started (the latitude and longitude are never written to the journal).

**If something is off.** A change on the card applies at once (the vision service is told); no restart. A limit that is switched on but cannot work (no usable place, no usable range) is **ignored and every pass is judged**, with a warning in the journal: a damaged setting never costs passes. The set hours follow the time zone in Settings; changing it moves them. The sun's times do not depend on it, only how they are shown.

## The daily email

TrafficPi can email a short report every day: how many vehicles **did not stop**, rolled through, stopped or were unclear at the stop signs since the last report, **each with its percentage** of all the passes judged, the stop rate (of those that could be told), the busiest hour, the vehicles counted and **the fastest estimated speed**, with a line when the camera was away for part of the time (so that a quiet day is not read as a safe one). The did-not-stop number is the headline, with the figure for the same length of time before it beside it. The numbers, and the way the percentages are rounded, are the Data page's, so the two cannot disagree.

**A speed is an estimate.** It is how fast a vehicle crossed the picture compared with its own size, turned into miles or kilometres an hour (the units in Settings) by what a car of that size is in metres. It is good to about 40%, it is rounded (to the nearest 5 above 10), it says what kind of vehicle it was and when, and it is not a measurement: a real one needs the speed calibration ([speed-calibration.md](speed-calibration.md)). The fastest of a day is the fastest *steady* half-second of any pass that day: a jump of the tracker is not counted. Passes made before the speed was added (the evening of 2026-10-09) have none. Once the camera is calibrated ([speed-calibration.md](speed-calibration.md)) the fastest is a speed measured on the road, shown to a whole mile or kilometre an hour without an "about", marked "Fastest speed" in place of "Fastest estimate", and followed by the sentence that the calibration has not yet been checked against a vehicle of known speed; a measured speed always outranks an estimate, whatever their sizes. A mail that covers three days or more (the Monday mail of a weekdays-only schedule) gives each day's fastest. **It is off until you set it up**: Setup page, "Daily email".

### Setting it up

1. **A Resend account** ([resend.com](https://resend.com); the free plan is enough for a few readers: 100 mails a day and 3,000 a month, each recipient counting as one, and one domain. Check their pricing page, it changes). Resend only sends from a domain you own and have proved it; its test sender delivers to the address the account was opened with and to nobody else.
2. **A domain**: in Resend, *Domains*, *Add Domain*. Use a **subdomain** such as `mail.yourdomain.com`, so that this mail never touches the mail of your main domain. Resend shows a few DNS records (SPF and DKIM; DMARC is optional): add them where your domain's DNS is managed, and wait until Resend says "Verified" (minutes, sometimes hours).
3. **An API key**: *API Keys*, *Create*, with the permission **Sending access**, limited to that domain. Copy it: Resend shows it once.
4. **In the dashboard**: Setup page, *Daily email*. Paste the key into "Resend API key" (or, on the Pi, run `backend/.venv/bin/trafficpi-admin set-mail-key`, which asks for it without showing it, so that it is not left in the shell history). Put a **sender address** on the verified domain (for example `report@mail.yourdomain.com`; any other address is refused), choose the time and the days, add the recipients, press **Send a test** next to your own address, and look in that inbox (and the spam folder). When it arrives, switch "Send the daily email" on and save.

**The key** is kept in `backend/data/mail.key`, readable by your user only, and nowhere else: it is not in the database, it is never shown again (not even in the dashboard), never written to a log, and goes to Resend and nowhere else. It is in the backups, which are private too. To change it, paste a new one; "Remove the key" deletes the file.

### What it does

- **When.** At the time you set, by the clock of the dashboard's time zone, on the days you tick. Each mail covers the time since the previous one: 24 hours on every day (23 or 25 on the days the clocks change), and the Monday mail of a weekdays-only schedule covers the weekend as well. The mail says exactly which stretch it covers. A change of the time or the days applies at the next look (every 30 seconds); nothing needs restarting. Switching it on after today's time has passed does not send today's mail: the first one is the next scheduled time.
- **If the Pi was off.** A mail that is due while the Pi is off or rebooting is sent when it is next up, if that is less than 6 hours late; after that it is skipped, and the Setup and System pages say so ("Not sent: the Pi was not running at the scheduled time"). A scheduled time that falls in the minute of the nightly reboot is simply sent when the Pi is back.
- **Who.** Up to 20 addresses; each gets **its own copy**, so nobody sees the others, and one that is refused fails alone. The same address added twice is one recipient. Each address shows how its last mail (or test) went, with the reason if it did not get through. Removing an address removes its history with it.
- **Once each.** Every copy has its own key for Resend, and each copy is claimed before it is sent, so a restart of the API in the middle of a send gives every recipient their mail once (Resend drops a repeat that carries the same key, for 24 hours).
- **When it does not get through.** A network failure, a rate limit or Resend's own trouble is tried again after 1, 5 and 15 minutes and then every 15 minutes, until the mail is 6 hours past its time; after that it is given up on, with the last reason. A refusal is not tried again. Either way the reason is shown next to the address (while it is being tried again, too). The usual refusals: *the domain is not verified* or *you can only send testing emails to your own address* (verify the domain, and send from an address on it); *API key is invalid* (paste it again); *daily quota exceeded* (the free plan allows 100 a day; it resets at midnight UTC).
- **Where to look.** The System page has a "Daily email" card (on or off, the next time, how the latest mail went), and the journal says what each look did: `journalctl -u trafficpi-api | grep -i "daily email"`.

### What leaves this machine

The recipients' addresses and the numbers in the report go to Resend, a third party, which delivers them; Resend's own privacy terms apply to what it holds. **No picture, clip or path is ever attached or linked**, and the HTML mail loads nothing when it is opened. The only address in it is the dashboard's, and only if you fill one in ("Link to this dashboard"); a Pi's own address works at home only, a tunnel's address works anywhere. Remove a recipient, or the key, and nothing more goes to them or to Resend.

### Looking before sending

`backend/.venv/bin/trafficpi-admin digest` prints what the mail would say now (for the past 24 hours) and sends nothing; `--html FILE` also writes the HTML to open in a browser. The same preview is the "Preview the mail" button on the card. To try the card without a provider at all, run the API with `--mail-transport file` (or `TRAFFICPI_MAIL_TRANSPORT=file`): each mail is then written to `outbox/` in the data directory (an `.html` to open in a browser and a `.txt` with the headers), no key is asked for, and an address that starts with `fail@` is refused so that a failure can be seen. That is what the demo and the browser test use.

## Backups

A backup is one file, `trafficpi-backup-YYYYMMDD-HHMMSS.tar.gz`, holding a consistent copy of the database (taken while the services run), the session key, the email provider's key if you set one, the reference picture and, if you ask, the pictures and clips. **It holds your password hash, the key that signs sessions and the email provider's key: keep it private.**

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

A defective RAM page on the development board showed up only as random, hard-to-explain failures. The installer runs `memtester` over 512 MB once, which can miss one bad page in 8 GB; `sudo scripts/find_bad_memory.py` scans all the free memory in chunks and names a bad page's address, and `sudo memtester 1G 1` is the quick look. A board that fails is a board whose other results nobody can trust.

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
| The Pi restarted and nobody asked it to | `journalctl -b -1 -n 100`: a restart that was asked for ends with systemd stopping the units; a log that just stops is a freeze or a power cut, and the Pi's hardware watchdog then resets the board about a minute later. `vcgencmd get_rsts` reads `20` on the development board after such a reset and after a normal reboot alike, so it does not tell them apart; the journal does. The System page's history shows the temperature, the Pi's own flags and the frame rate for the minutes before. Then: a different 27 W supply, the cooler and the ribbon cable, and keep other heavy work off the Pi while it counts. A second one without a cause is a reason to suspect the board. |
| The camera view moved | The notice on every page says so; put it back, or draw the zones again on a new reference picture (Setup). |
