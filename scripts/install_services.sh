#!/usr/bin/env bash
# Install the two systemd services for this checkout.
#
#   scripts/install_services.sh             copy the units to /etc/systemd/system (not started)
#   scripts/install_services.sh --enable    also enable them at boot and start them now
#   scripts/install_services.sh --remove    stop, disable and delete them
#
# Options: --user NAME (default: you), --data-dir DIR (default: <checkout>/backend/data,
# where the commands run from backend/ keep their data too).
# Uses sudo. Once the services run, they own the camera and the accelerator: stop them first
# (sudo systemctl stop trafficpi-vision) before running dry-run, snapshot or record by hand.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
user="$(id -un)"
data_dir="$root/backend/data"
action=install
units=(trafficpi-vision.service trafficpi-api.service)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --enable) action=enable ;;
    --remove) action=remove ;;
    --user) user="$2"; shift ;;
    --data-dir) data_dir="$(realpath -m "$2")"; shift ;;
    -h|--help) sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if [[ "$action" == remove ]]; then
  for unit in "${units[@]}"; do
    sudo systemctl disable --now "$unit" 2>/dev/null || true
    sudo rm -f "/etc/systemd/system/$unit"
  done
  sudo systemctl daemon-reload
  echo "removed ${units[*]}"
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
sudo systemctl daemon-reload
echo "installed ${units[*]} (user $user, data in $data_dir)"

if [[ "$action" == enable ]]; then
  sudo systemctl enable --now "${units[@]}"
  echo "enabled and started; logs: journalctl -u trafficpi-vision -u trafficpi-api -f"
fi
