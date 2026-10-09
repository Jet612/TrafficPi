#!/usr/bin/env bash
# Install the two systemd services for this checkout.
#
#   scripts/install_services.sh             copy the units to /etc/systemd/system (not started)
#   scripts/install_services.sh --enable    also enable them at boot and start them now
#   scripts/install_services.sh --remove    stop, disable and delete them
#
# Options: --user NAME (default: you), --data-dir DIR (default: <checkout>/backend/data,
# where the commands run from backend/ keep their data too).
#   --reboot-at HH:MM   also install a timer that reboots the Pi every day at that local time
#                       (with --enable it is switched on too; a Pi that came up less than ten
#                       minutes before is left alone). Example: --enable --reboot-at 03:00
#   --reboot-off        take the daily reboot out again and do nothing else
#   --persistent-journal  also keep the journal across reboots, at most 256 MB and a month of it.
#                       Raspberry Pi OS keeps it in RAM, so the log of a reboot (the nightly one
#                       included) is gone afterwards and `journalctl -b -1` shows nothing
#   --journal-off       put Raspberry Pi OS's own setting (a journal in RAM) back, and do nothing else
# Uses sudo. Once the services run, they own the camera and the accelerator: stop them first
# (sudo systemctl stop trafficpi-vision) before running dry-run, snapshot or record by hand.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
user="$(id -un)"
data_dir="$root/backend/data"
action=install
units=(trafficpi-vision.service trafficpi-api.service)
reboot_units=(trafficpi-reboot.service trafficpi-reboot.timer)
reboot_at=""
journal=0
journal_conf=/etc/systemd/journald.conf.d/50-trafficpi.conf

while [[ $# -gt 0 ]]; do
  case "$1" in
    --enable) action=enable ;;
    --remove) action=remove ;;
    --reboot-at) reboot_at="$2"; shift ;;
    --reboot-off) action=reboot-off ;;
    --persistent-journal) journal=1 ;;
    --journal-off) action=journal-off ;;
    --user) user="$2"; shift ;;
    --data-dir) data_dir="$(realpath -m "$2")"; shift ;;
    -h|--help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if [[ -n "$reboot_at" && ! "$reboot_at" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
  echo "--reboot-at wants a time of day as HH:MM, like 03:00 (got '$reboot_at')" >&2
  exit 2
fi

remove_reboot_units() {
  sudo systemctl disable --now trafficpi-reboot.timer 2>/dev/null || true
  for unit in "${reboot_units[@]}"; do
    sudo rm -f "/etc/systemd/system/$unit"
  done
}

remove_journal_conf() { # what was written to the journal stays on disk until it ages out
  if [[ -e "$journal_conf" ]]; then
    sudo rm -f "$journal_conf"
    sudo rmdir --ignore-fail-on-non-empty "$(dirname "$journal_conf")"
    sudo systemctl restart systemd-journald
    echo "removed $journal_conf: the journal is kept in RAM again, as Raspberry Pi OS has it"
  fi
}

if [[ "$action" == reboot-off ]]; then
  remove_reboot_units
  sudo systemctl daemon-reload
  echo "removed ${reboot_units[*]}: the Pi is not rebooted every day any more"
  exit 0
fi

if [[ "$action" == journal-off ]]; then
  remove_journal_conf
  exit 0
fi

if [[ "$action" == remove ]]; then
  for unit in "${units[@]}"; do
    sudo systemctl disable --now "$unit" 2>/dev/null || true
    sudo rm -f "/etc/systemd/system/$unit"
  done
  remove_reboot_units
  sudo systemctl daemon-reload
  remove_journal_conf
  echo "removed ${units[*]} ${reboot_units[*]}"
  exit 0
fi

[[ -x "$root/backend/.venv/bin/trafficpi-vision" ]] \
  || { echo "no virtualenv in $root/backend: run 'uv sync' there first" >&2; exit 1; }
id "$user" >/dev/null
mkdir -p "$data_dir"
if [[ "$(stat -c %U "$data_dir")" != "$user" ]]; then
  sudo chown "$user" "$data_dir"
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
for unit in "${units[@]}"; do
  sed -e "s|@USER@|$user|g" -e "s|@INSTALL_DIR@|$root|g" -e "s|@DATA_DIR@|$data_dir|g" \
    "$root/deploy/systemd/$unit" > "$tmp/$unit"
  systemd-analyze verify "$tmp/$unit"
  sudo install -m 644 "$tmp/$unit" "/etc/systemd/system/$unit"
done
if [[ -n "$reboot_at" ]]; then
  for unit in "${reboot_units[@]}"; do
    sed -e "s|@REBOOT_AT@|$reboot_at|g" "$root/deploy/systemd/$unit" > "$tmp/$unit"
  done
  systemd-analyze verify "$tmp/trafficpi-reboot.timer" "$tmp/trafficpi-reboot.service"
  sudo install -m 644 "$tmp/trafficpi-reboot.service" "$tmp/trafficpi-reboot.timer" /etc/systemd/system/
fi
sudo systemctl daemon-reload
echo "installed ${units[*]} (user $user, data in $data_dir)"
[[ -z "$reboot_at" ]] || echo "installed ${reboot_units[*]} (every day at $reboot_at, local time)"

if (( journal )); then
  sudo install -D -m 644 "$root/deploy/journald/50-trafficpi.conf" "$journal_conf"
  sudo systemctl restart systemd-journald # the services' log streams are kept across this
  sudo journalctl --flush                 # and what this boot has said so far moves to the disk
  echo "installed $journal_conf: the journal survives reboots now ($(journalctl --disk-usage | sed 's/^Archived and active journals take up //; s/ in the file system.//') so far)"
fi

if [[ "$action" == enable ]]; then
  sudo systemctl enable --now "${units[@]}"
  echo "enabled and started; logs: journalctl -u trafficpi-vision -u trafficpi-api -f"
  if [[ -n "$reboot_at" ]]; then
    sudo systemctl enable trafficpi-reboot.timer
    sudo systemctl restart trafficpi-reboot.timer # also picks up a new time if it was running
    systemctl list-timers trafficpi-reboot.timer --no-pager
  fi
fi
