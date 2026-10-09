# Installing TrafficPi

From a blank SD card to a dashboard that counts traffic. This page is for a fresh install; the exact steps that built the development machine, with the results they gave, are in [dev-setup.md](dev-setup.md), and day-to-day running is in [operations.md](operations.md).

> **How much of this has been tried.** The installer (`scripts/install.sh`) has been checked with `shellcheck` and with `--dry-run` on the development Pi (it lists what it would do and changes nothing), and its package names were looked up in a clean Debian 13 arm64 root with the Raspberry Pi package repository added (all of them resolve). **It has not yet been run for real in a clean root or on a blank SD card, nor a second time on the development Pi to show that it then changes nothing**: those runs are the last steps of its checklist (todo.md, M8 step 7) and this note will change when they are done. A step on a real fresh image may still surprise it; if one does, the output says where, and the notes at the end say what to look at. Only the Hailo-8L (13 TOPS) and the Camera Module 3 have been tried.

## What you need

- A **Raspberry Pi 5** (4 GB works; 8 GB was used for development), its **27 W USB-C power supply** and an **Active Cooler**: the board gets hot when it is busy.
- A **Raspberry Pi AI HAT+** with a **Hailo-8L** (13 TOPS; the 26 TOPS Hailo-8 should work and has not been tried). The AI HAT+ 2 (Hailo-10H) is a different chip and is not supported.
- A **Camera Module 3** (the IMX708) and the camera cable for the Pi 5.
- A **microSD card** of 32 GB or more (the system and toolchain take about 11 GB; trails, pictures and backups are small by comparison).
- A network cable or Wi-Fi, a computer to flash the card from, and a place for the camera that sees the road.

## 1. The card

Flash **Raspberry Pi OS Lite (64-bit)**, the Debian 13 "Trixie" one, with Raspberry Pi Imager. In its settings (the gear, or "Edit settings") give the Pi a host name, make a user, set the Wi-Fi if you use it, and turn SSH on (a key is better than a password). The user you make here is the one TrafficPi runs as, and it needs `sudo` (the first user has it).

## 2. The hardware

With the Pi switched off, fit the AI HAT+ (its PCIe ribbon and the standoffs) and the camera (the Pi 5's camera connectors are small: lift the latch, push the cable in with the contacts towards the board, press the latch down). Put the Active Cooler on first if the HAT's height allows it, as the Raspberry Pi guides show.

## 3. The first boot and an update

Start it, log in over SSH, and bring the system up to date **before** installing, then reboot, so that the kernel and its headers are the new ones when the Hailo driver is built for the running kernel:

```
sudo apt update && sudo apt full-upgrade -y && sudo reboot
```

## 4. The installer

```
sudo apt-get install -y git
git clone https://github.com/Jet612/TrafficPi.git
cd TrafficPi
scripts/install.sh
```

(Run it as your own user, not as root; it asks `sudo` for what needs it. `scripts/install.sh --dry-run` first shows what it would do and changes nothing.) It takes roughly ten to twenty minutes, most of it downloads and the dashboard's build. It does this, in nine steps, each of which it skips if it is already done:

1. **Looks at the machine**: not root, 64-bit, Debian 13 on a Raspberry Pi 5, 4 GB of disk free, and the internet reachable. Anything else it stops on (or, with `--ignore-os`, warns about).
2. **Installs from apt** the accelerator's driver and runtime (the four HailoRT packages, not the `hailo-all` bundle, which brings 650 packages TrafficPi does not use), Picamera2, ffmpeg and `memtester`; stages a bootloader update if there is one (it applies at the next reboot); and **holds** the four Hailo packages so that an upgrade cannot change them one by one.
3. **Installs uv and Node** in `~/.local`, at the versions in the script, each checked against the checksum written in the script (a download that differs is refused), and pnpm through Corepack.
4. **Installs the locked Python packages** into `backend/.venv`, a virtualenv that can see the apt packages (Picamera2 and the HailoRT bindings).
5. **Builds the dashboard** into `dashboard/out` (unless that was built from the very same version).
6. **Tests 512 MB of memory** once and writes the result to `backend/data/memtest.json`. (A defective page on the development board showed up only as random failures. One pass over a few hundred MB can miss a single bad page in 8 GB; `scripts/find_bad_memory.py` scans all the free memory.)
7. **Asks for the dashboard's password** (at least 10 characters, for the user `admin`), or reads it from the first line of the file given with `--password-file`. If a user exists, the password is left alone.
8. **Installs and starts the two services** (`trafficpi-vision` and `trafficpi-api`, enabled at boot), the persistent journal and a nightly backup at 03:30. `--reboot-at 03:00` adds a daily reboot.
9. **Checks that it works** (`scripts/check_boot.sh`) and prints the address.

Options (`scripts/install.sh --help` has them all): `--reboot-at HH:MM`, `--backup-at HH:MM` or `none`, `--no-persistent-journal`, `--memtest MB` (0 skips it), `--password-file FILE`, `--data-dir DIR`, `--skip-dashboard-build`, `--ignore-os` (unsupported).

## 5. After it

If the installer says a reboot is due (a first install often needs one: the driver was built, a bootloader update is waiting), `sudo reboot`, and then run `scripts/check_boot.sh`: eleven checks, all PASS. Then open **`http://<host name>.local:8000`** on any computer or phone on the same network, sign in as `admin`, and on the **Setup** page draw a counting line across the road (and, for a stop sign, an approach zone and a stop line). The Live page shows what the camera sees with the lines on it.

The camera should look at the road from a fixed place. Through a window, reflections of the room can fill the picture at night: keep the room dark at the lens, shade it, or put the camera outside. If the camera is moved after the zones were drawn, the dashboard says so on every page; save a new reference picture on the Setup page after you put it right.

## What the installer put on the machine

Packages (apt), held Hailo packages, `~/.local/bin/{uv,uvx,node,npm,npx,corepack,pnpm}` and `~/.local/share/node`, `backend/.venv`, `dashboard/node_modules` and `dashboard/out`, the data directory (`backend/data`, readable by you only), the systemd units `trafficpi-vision.service`, `trafficpi-api.service`, `trafficpi-backup.service` and `.timer` (and `trafficpi-reboot.*` if asked) in `/etc/systemd/system`, and `/etc/systemd/journald.conf.d/50-trafficpi.conf`. **The units name this checkout's folder: if you move or rename it, run `scripts/install.sh` again.**

To take TrafficPi off again: `scripts/install_services.sh --remove` (stops and deletes the units and the extras; the data stays), then delete the checkout and, if you want, `backend/data`. The apt packages and `~/.local` stay until you remove them (`sudo apt-mark unhold hailort hailort-pcie-driver python3-hailort hailo-models` first).

## Updating and running

[operations.md](operations.md): `scripts/update.sh --pull`, backups, restoring, what the watchdogs do, the disk, the heat. Reaching the dashboard from outside your network: [remote-access.md](remote-access.md). What is and is not protected: [security.md](security.md).

## If the installer stops

It stops at the first thing that fails and says what; running it again picks up where it left off. The likely ones:

| It says | Usually |
|---|---|
| `cannot reach github.com` or `archive.raspberrypi.com` | No internet, or a proxy or DNS problem on the Pi. |
| `this is made for Debian 13 on a Raspberry Pi 5` | An older image (Bookworm) or another board. The package names and the driver differ; `--ignore-os` goes on at your own risk. |
| `the download of ... does not match its checksum` | A damaged or changed download: run it again; if it repeats, do not go on. |
| An apt error about the driver (`dkms`) | The kernel headers do not match the running kernel: `sudo apt full-upgrade`, `sudo reboot`, run the installer again. |
| `memtester reported a problem` | The memory has a fault (plan.md R16). Everything else on this board is then suspect. |
| `check_boot.sh` fails | After a first install, reboot and run it again. The line that says FAIL names the check: [operations.md](operations.md), "When something is wrong". |
