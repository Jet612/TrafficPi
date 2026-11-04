#!/usr/bin/env bash
# Install TrafficPi on a Raspberry Pi 5 with Raspberry Pi OS Lite (64-bit, Debian 13 "Trixie"), a
# Camera Module 3 and a Hailo AI HAT+. Run it from the checkout, as your own user (not as root; it
# asks sudo when it needs to):
#
#   sudo apt-get install -y git
#   git clone https://github.com/Jet612/TrafficPi.git && cd TrafficPi && scripts/install.sh
#
# It is safe to run again: what is already in place is left as it is. See docs/install.md.
#
# Options:
#   --dry-run             check the machine and print what would be done; change nothing
#   --reboot-at HH:MM     also reboot the Pi every day at that time (off by default)
#   --backup-at HH:MM     back up the database every day at that time (default 03:30; "none" skips)
#   --link-watch          also bring back a Wi-Fi link that stays lost, up to a reboot (off by default;
#                         see scripts/link_watch.sh)
#   --no-persistent-journal  leave the log in RAM, as Raspberry Pi OS has it (it is kept on the disk
#                         by default, so that a reboot's log can be read afterwards)
#   --memtest MB          test that much RAM once at the end (default 512; 0 skips). It can miss one
#                         bad page in 8 GB: scripts/find_bad_memory.py scans all of the free memory
#   --password-file FILE  take the dashboard password from the first line of FILE, instead of asking
#   --skip-dashboard-build  do not build the dashboard (dashboard/out is already there)
#   --data-dir DIR        where the database and pictures go (default: backend/data in the checkout)
#   --ignore-os           go on though this is not Debian 13 on a Raspberry Pi 5 (unsupported)
set -euo pipefail

# What is installed in your home directory, at exactly these versions: the checksums are those
# published with the releases, checked here against the files, so a changed download is refused.
# (CI pins the same uv; Node is the 22 line.)
UV_VERSION=0.12.24
UV_SHA256=5231be65f496304623895dacdbf1de8504fec90303684bdf05805aa34414dd21
NODE_VERSION=v22.23.3
NODE_SHA256=a44aeb94849a299b22df10b9e622ec2f605c2183501bc40590705131de7c740f

# The apt packages (the four HailoRT ones, not the hailo-all bundle with TAPPAS).
HAILO_PACKAGES=(hailort hailort-pcie-driver python3-hailort hailo-models)
# (xz-utils unpacks Node's tarball, python3 is what uv builds the virtualenv from, kmod gives lsmod.
# Raspberry Pi OS Lite has them; naming them keeps this from depending on that.)
APT_PACKAGES=(dkms linux-headers-rpi-2712 "${HAILO_PACKAGES[@]}" python3-picamera2 python3-numpy
  sqlite3 ffmpeg memtester curl ca-certificates git xz-utils python3 kmod)

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
data_dir="$root/backend/data"
dry=0
reboot_at=""
backup_at="03:30"
link_watch=0
persistent_journal=1
memtest_mb=512
password_file=""
build_dashboard=1
ignore_os=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) dry=1 ;;
    --reboot-at) reboot_at="${2:?--reboot-at wants a time}"; shift ;;
    --backup-at) backup_at="${2:?--backup-at wants a time or none}"; shift ;;
    --link-watch) link_watch=1 ;;
    --no-persistent-journal) persistent_journal=0 ;;
    --memtest) memtest_mb="${2:?--memtest wants a number of megabytes}"; shift ;;
    --password-file) password_file="${2:?--password-file wants a file}"; shift ;;
    --skip-dashboard-build) build_dashboard=0 ;;
    --data-dir) data_dir="$(realpath -m "${2:?--data-dir wants a folder}")"; shift ;;
    --ignore-os) ignore_os=1 ;;
    -h|--help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

time_of_day='^([01][0-9]|2[0-3]):[0-5][0-9]$'
if [[ -n "$reboot_at" && ! "$reboot_at" =~ $time_of_day ]]; then
  echo "--reboot-at wants a time of day as HH:MM, like 03:00 (got '$reboot_at')" >&2; exit 2
fi
if [[ "$backup_at" != none && ! "$backup_at" =~ $time_of_day ]]; then
  echo "--backup-at wants a time of day as HH:MM, like 03:30, or none (got '$backup_at')" >&2; exit 2
fi
if [[ ! "$memtest_mb" =~ ^[0-9]+$ ]]; then
  echo "--memtest wants a number of megabytes, like 512 (got '$memtest_mb')" >&2; exit 2
fi
if [[ -n "$password_file" && ! -r "$password_file" ]]; then
  echo "cannot read the password file $password_file" >&2; exit 2
fi

step=0
steps=9
say() { printf '\n== %d/%d  %s\n' "$((++step))" "$steps" "$1"; }
note() { printf '   %s\n' "$*"; }
warn() { printf '   WARNING: %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# Anything that changes the machine goes through here, so that --dry-run can say it instead.
run() {
  if (( dry )); then printf '   + %s\n' "$*"; else "$@"; fi
}

export PATH="$HOME/.local/bin:$PATH"

# --- 1. what this machine is --------------------------------------------------------------------

preflight() {
  say "Checking this machine"
  (( EUID != 0 )) || die "do not run this as root: run it as your own user; it asks sudo for what needs it"
  command -v sudo >/dev/null || die "sudo is needed"
  if ! sudo -n true 2>/dev/null; then
    note "sudo will ask for your password now (and again if a step takes long)"
    sudo -v || die "sudo did not work"
  fi
  [[ "$(uname -m)" == aarch64 ]] || die "this is $(uname -m), not a 64-bit Raspberry Pi OS (aarch64)"
  local os model
  os="$(. /etc/os-release && echo "${ID:-?} ${VERSION_ID:-?}")"
  model="$({ tr -d '\0' < /proc/device-tree/model; } 2>/dev/null || echo unknown)"
  note "$os, $model"
  if [[ "$os" != "debian 13" || "$model" != *"Raspberry Pi 5"* ]]; then
    if (( ignore_os )); then warn "this is not Debian 13 on a Raspberry Pi 5; going on because of --ignore-os"
    else die "this is made for Debian 13 on a Raspberry Pi 5 (this is: $os, $model). --ignore-os goes on anyway"; fi
  fi
  local free_gb ram_mb
  free_gb="$(df --output=avail -BG "$root" | tail -1 | tr -dc 0-9)"
  ram_mb="$(awk '/MemTotal/ { print int($2 / 1024) }' /proc/meminfo)"
  (( free_gb >= 4 )) || die "only ${free_gb} GB free here; about 4 GB are needed (the toolchain, the build and the packages)"
  (( ram_mb >= 3500 )) || warn "only ${ram_mb} MB of RAM: building the dashboard may run out of memory (add swap, or use a prebuilt dashboard with --skip-dashboard-build)"
  curl -fsSI --max-time 15 https://github.com >/dev/null || die "cannot reach github.com (the internet?)"
  curl -fsSI --max-time 15 http://archive.raspberrypi.com/debian/ >/dev/null || die "cannot reach archive.raspberrypi.com"
  note "ok: ${free_gb} GB free, ${ram_mb} MB of RAM, the internet answers"
}

# --- 2. packages from apt ------------------------------------------------------------------------

install_packages() {
  say "Installing packages from apt (the accelerator's driver and runtime, Picamera2, ffmpeg)"
  run sudo apt-get update
  # The bootloader update that the AI HAT+ instructions ask for; it applies at the next reboot.
  if command -v rpi-eeprom-update >/dev/null; then run sudo rpi-eeprom-update -a || true; fi
  run sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${APT_PACKAGES[@]}"
  # Driver, runtime and models must be upgraded together: an apt upgrade must not do one of them.
  run sudo apt-mark hold "${HAILO_PACKAGES[@]}"
  note "held: ${HAILO_PACKAGES[*]} (to upgrade them together: apt-mark unhold, upgrade, test, hold)"
  if ! lsmod | grep -q '^hailo_pci'; then
    run sudo modprobe hailo_pci || warn "the driver is not loaded yet; a reboot at the end will load it"
  fi
}

# --- 3. uv, Node and pnpm in your home directory -------------------------------------------------

fetch_checked() { # fetch_checked URL FILE SHA256: download, and refuse a file whose checksum differs
  curl -fsSL -o "$2" "$1"
  echo "$3  $2" | sha256sum -c - >/dev/null || die "the download of $1 does not match its checksum"
}

install_toolchain() {
  say "Installing uv, Node and pnpm in ~/.local (pinned versions, checked against their checksums)"
  run mkdir -p "$HOME/.local/bin" "$HOME/.local/share/node"
  if [[ "$(uv --version 2>/dev/null | awk '{print $2}')" == "$UV_VERSION" ]]; then
    note "uv $UV_VERSION is there"
  else
    note "uv $UV_VERSION"
    if (( dry )); then printf '   + download, check and install uv %s\n' "$UV_VERSION"; else
      local tmp; tmp="$(mktemp -d)"
      fetch_checked "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-aarch64-unknown-linux-gnu.tar.gz" \
        "$tmp/uv.tar.gz" "$UV_SHA256"
      tar -xzf "$tmp/uv.tar.gz" -C "$tmp"
      install -m 0755 "$tmp/uv-aarch64-unknown-linux-gnu/uv" "$tmp/uv-aarch64-unknown-linux-gnu/uvx" "$HOME/.local/bin/"
      rm -rf "$tmp"
    fi
  fi
  local node_dir="$HOME/.local/share/node/node-$NODE_VERSION-linux-arm64"
  if [[ -x "$node_dir/bin/node" ]]; then
    note "Node $NODE_VERSION is there"
  else
    note "Node $NODE_VERSION"
    if (( dry )); then printf '   + download, check and unpack Node %s\n' "$NODE_VERSION"; else
      local tmp; tmp="$(mktemp -d)"
      fetch_checked "https://nodejs.org/dist/$NODE_VERSION/node-$NODE_VERSION-linux-arm64.tar.xz" \
        "$tmp/node.tar.xz" "$NODE_SHA256"
      tar -xJf "$tmp/node.tar.xz" -C "$HOME/.local/share/node"
      rm -rf "$tmp"
    fi
  fi
  run ln -sfn "$node_dir" "$HOME/.local/share/node/current"
  for tool in node npm npx corepack; do
    run ln -sfn "$HOME/.local/share/node/current/bin/$tool" "$HOME/.local/bin/$tool"
  done
  # pnpm comes through Corepack, at the version the dashboard's package.json pins.
  run corepack enable --install-directory "$HOME/.local/bin" pnpm
  case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) warn "$HOME/.local/bin is not on your PATH" ;; esac
}

# --- 4. the backend ------------------------------------------------------------------------------

install_backend() {
  say "Installing the backend's locked Python packages"
  if (( dry )); then printf '   + (cd backend && uv venv --system-site-packages && uv sync --locked)\n'; return; fi
  cd "$root/backend"
  # The virtualenv must see the packages from apt (Picamera2, the HailoRT bindings).
  if ! grep -qs '^include-system-site-packages = true' .venv/pyvenv.cfg; then
    rm -rf .venv
    uv venv --system-site-packages
  fi
  uv sync --locked
}

# --- 5. the dashboard ----------------------------------------------------------------------------

build_the_dashboard() {
  say "Building the dashboard (a few minutes)"
  if (( ! build_dashboard )); then note "skipped (--skip-dashboard-build)"; return; fi
  local tree="" stamp="$root/dashboard/out/.built-from"
  if git -C "$root" rev-parse --git-dir >/dev/null 2>&1 && [[ -z "$(git -C "$root" status --porcelain -- dashboard)" ]]; then
    tree="$(git -C "$root" rev-parse HEAD:dashboard)"
  fi
  if [[ -n "$tree" && -f "$stamp" && "$(cat "$stamp")" == "$tree" ]]; then
    note "dashboard/out is built from this very version already"
    return
  fi
  if (( dry )); then printf '   + (cd dashboard && pnpm install --frozen-lockfile && pnpm build)\n'; return; fi
  cd "$root/dashboard"
  pnpm install --frozen-lockfile
  pnpm build
  [[ -z "$tree" ]] || echo "$tree" > "$stamp"
}

# --- 6. a memory test, before the services take the memory --------------------------------------

run_memtest() {
  say "Testing the memory ($memtest_mb MB, once)"
  if (( memtest_mb == 0 )); then note "skipped (--memtest 0)"; return; fi
  # A defective RAM page showed up on the development board only as random, hard-to-explain
  # failures. One pass over a few hundred MB is a quick look, not a guarantee.
  local ok=false out
  if (( dry )); then printf '   + sudo memtester %sM 1\n' "$memtest_mb"; return; fi
  if out="$(sudo memtester "${memtest_mb}M" 1 2>&1)"; then
    ok=true
    note "memtester found nothing wrong"
  else
    warn "memtester reported a problem; see below. A board with bad RAM gives results nobody can trust"
    printf '%s\n' "$out" | tail -n 12 | sed 's/^/      /'
  fi
  mkdir -p "$data_dir" && chmod 700 "$data_dir"
  printf '{"at": "%s", "mb": %s, "passes": 1, "ok": %s}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$memtest_mb" "$ok" \
    > "$data_dir/memtest.json"
}

# --- 7. the dashboard's password -----------------------------------------------------------------

set_password() {
  say "Setting the dashboard's password"
  if (( dry )); then printf '   + trafficpi-admin set-password (unless a user exists already)\n'; return; fi
  cd "$root/backend"
  mkdir -p "$data_dir" && chmod 700 "$data_dir"
  if [[ -n "$(uv run --no-sync trafficpi-admin --data-dir "$data_dir" users)" ]]; then
    note "a user exists already; its password is left as it is (change it with: trafficpi-admin set-password)"
  elif [[ -n "$password_file" ]]; then
    head -n 1 "$password_file" | uv run --no-sync trafficpi-admin --data-dir "$data_dir" set-password --password-stdin
  else
    note "choose a password of at least 10 characters for the user 'admin'"
    uv run --no-sync trafficpi-admin --data-dir "$data_dir" set-password
  fi
}

# --- 8. the services -----------------------------------------------------------------------------

install_services() {
  say "Installing and starting the services"
  local options=(--enable --data-dir "$data_dir")
  [[ -z "$reboot_at" ]] || options+=(--reboot-at "$reboot_at")
  [[ "$backup_at" == none ]] || options+=(--backup-at "$backup_at")
  (( ! link_watch )) || options+=(--link-watch)
  (( ! persistent_journal )) || options+=(--persistent-journal)
  (( ! dry )) || options+=(--dry-run)
  "$root/scripts/install_services.sh" "${options[@]}"
}

# --- 9. is it working? ---------------------------------------------------------------------------

finish() {
  say "Checking that it works"
  if (( dry )); then printf '   + scripts/check_boot.sh\n'; return; fi
  local failed=0
  sudo -n true 2>/dev/null || sudo -v # the checks use sudo; ask only if it would ask
  # The services need a few seconds to open the camera and the accelerator.

  for _ in $(seq 1 40); do
    curl -fsS --max-time 3 http://127.0.0.1:8001/health >/dev/null 2>&1 && break
    sleep 2
  done
  "$root/scripts/check_boot.sh" || failed=1
  local addresses
  addresses="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.' | head -3 | sed 's|^|http://|; s|$|:8000|' | tr '\n' ' ')"
  printf '\nThe dashboard is at: http://%s.local:8000  %s\n' "$(hostname)" "$addresses"
  if (( failed )); then
    printf '\nSome checks failed (above). After a first install a reboot is often what is missing:\n'
    printf '  sudo reboot     then, from the checkout:  scripts/check_boot.sh\n'
    return 1
  fi
  printf '\nDone. Next: open the dashboard, sign in as admin, and draw a counting line on the Setup page.\n'
  if [[ -f /var/run/reboot-required || "$(rpi-eeprom-update 2>/dev/null | grep -c 'UPDATE REQUIRED')" != 0 ]]; then
    printf 'A reboot is due (a bootloader or kernel update is waiting): sudo reboot\n'
  fi
}

(( dry )) && printf 'DRY RUN: the machine is only looked at; lines that start with + are what would be run.\n'
preflight
install_packages
install_toolchain
install_backend
build_the_dashboard
run_memtest
set_password
install_services
finish
