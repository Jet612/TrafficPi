#!/usr/bin/env bash
# Install the systemd services for this checkout, and the extras around them.
#
#   scripts/install_services.sh             copy the units to /etc/systemd/system (not started)
#   scripts/install_services.sh --enable    also enable them at boot and start them now
#   scripts/install_services.sh --remove    stop, disable and delete them (and every extra below)
#
# Options: --user NAME (default: you), --data-dir DIR (default: <checkout>/backend/data,
# where the commands run from backend/ keep their data too).
#   --dry-run           change nothing: print what would be done, and for each unit how it differs
#                       from the one installed now
#   --restart           with --enable: restart the services even if they run, so that changed units
#                       and new code take effect now (nothing is counted for about 10 seconds)
#   --reboot-at HH:MM   also install a timer that reboots the Pi every day at that local time
#                       (with --enable it is switched on too; a Pi that came up less than ten
#                       minutes before is left alone). Example: --enable --reboot-at 03:00
#   --reboot-off        take the daily reboot out again and do nothing else
#   --backup-at HH:MM   also install a timer that backs up the database every day at that local
#                       time (with --enable it is switched on too): one .tar.gz of the database and
#                       the files beside it, in <data dir>/backups unless --backup-to says another
#                       place. See `trafficpi-admin backup --help` and `restore --help`
#   --backup-keep N     how many daily backups stay (default 7)
#   --backup-to DIR     put them in DIR (an existing folder of yours: a USB disk, a mounted share)
#   --backup-off        take the daily backup out again and do nothing else
#   --link-watch        also install a timer that looks at the Wi-Fi link every minute and, while it
#                       stays lost, switches the radio off and on (10 minutes), reloads the driver
#                       (15) and reboots the Pi (25, at most once in 6 hours). With --enable it is
#                       switched on too. See scripts/link_watch.sh
#   --link-watch-off    take that timer out again and do nothing else
#   --trust-proxy       also tell the API to believe the client address that a proxy on this machine
#                       (cloudflared) passes on, instead of 127.0.0.1 for everyone: it writes the
#                       drop-in /etc/systemd/system/trafficpi-api.service.d/10-remote.conf. Needed
#                       for the login limits to tell visitors apart behind a Cloudflare Tunnel; see
#                       docs/remote-access.md. Use --restart with --enable to apply it now
#   --trust-proxy-off   take that drop-in out again and do nothing else
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
dry=0
restart=0
units=(trafficpi-vision.service trafficpi-api.service)
reboot_units=(trafficpi-reboot.service trafficpi-reboot.timer)
reboot_at=""
backup_units=(trafficpi-backup.service trafficpi-backup.timer)
backup_at=""
backup_keep=7
backup_to=""
link_units=(trafficpi-link-watch.service trafficpi-link-watch.timer)
link_script=/usr/local/sbin/trafficpi-link-watch
link_watch=0
journal=0
journal_conf=/etc/systemd/journald.conf.d/50-trafficpi.conf
trust_proxy=0
remote_conf=/etc/systemd/system/trafficpi-api.service.d/10-remote.conf

while [[ $# -gt 0 ]]; do
  case "$1" in
    --enable) action=enable ;;
    --remove) action=remove ;;
    --dry-run) dry=1 ;;
    --restart) restart=1 ;;
    --reboot-at) reboot_at="${2:?--reboot-at wants a time}"; shift ;;
    --reboot-off) action="reboot-off" ;;
    --backup-at) backup_at="${2:?--backup-at wants a time}"; shift ;;
    --backup-keep) backup_keep="${2:?--backup-keep wants a number}"; shift ;;
    --backup-to) backup_to="${2:?--backup-to wants a folder}"; shift ;;
    --backup-off) action="backup-off" ;;
    --link-watch) link_watch=1 ;;
    --link-watch-off) action="link-watch-off" ;;
    --trust-proxy) trust_proxy=1 ;;
    --trust-proxy-off) action="trust-proxy-off" ;;
    --persistent-journal) journal=1 ;;
    --journal-off) action="journal-off" ;;
    --user) user="${2:?--user wants a name}"; shift ;;
    --data-dir) data_dir="$(realpath -m "${2:?--data-dir wants a folder}")"; shift ;;
    -h|--help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

time_of_day='^([01][0-9]|2[0-3]):[0-5][0-9]$'
if [[ -n "$reboot_at" && ! "$reboot_at" =~ $time_of_day ]]; then
  echo "--reboot-at wants a time of day as HH:MM, like 03:00 (got '$reboot_at')" >&2
  exit 2
fi
if [[ -n "$backup_at" && ! "$backup_at" =~ $time_of_day ]]; then
  echo "--backup-at wants a time of day as HH:MM, like 03:30 (got '$backup_at')" >&2
  exit 2
fi
if [[ ! "$backup_keep" =~ ^[1-9][0-9]*$ ]]; then
  echo "--backup-keep wants a whole number of backups, like 7 (got '$backup_keep')" >&2
  exit 2
fi
if [[ -n "$backup_to" ]]; then
  backup_to="$(realpath -m "$backup_to")"
  if [[ "$backup_to" =~ [[:space:]\|] || ! -d "$backup_to" ]]; then
    echo "--backup-to wants an existing folder whose name has no spaces or | in it (got '$backup_to')" >&2
    exit 2
  fi
fi

did="installed"; removed="removed"; started="enabled and started"
if (( dry )); then did="would install"; removed="would remove"; started="would enable and start"; fi

# Anything that changes the system goes through here, so that --dry-run can say it instead.
run() {
  if (( dry )); then printf '+ %s\n' "$*"; else "$@"; fi
}

remove_units() { # remove_units TIMER UNIT...: stop and delete a timer and what it starts
  local timer=$1; shift
  run sudo systemctl disable --now "$timer" 2>/dev/null || true
  for unit in "$@"; do
    run sudo rm -f "/etc/systemd/system/$unit"
  done
}

remove_link_watch() {
  remove_units trafficpi-link-watch.timer "${link_units[@]}"
  run sudo rm -f "$link_script"
  run sudo rm -rf /run/trafficpi-link-watch /var/lib/trafficpi-link-watch
}

remove_journal_conf() { # what was written to the journal stays on disk until it ages out
  if [[ -e "$journal_conf" ]]; then
    run sudo rm -f "$journal_conf"
    run sudo rmdir --ignore-fail-on-non-empty "$(dirname "$journal_conf")"
    run sudo systemctl restart systemd-journald
    echo "$removed $journal_conf: the journal is kept in RAM again, as Raspberry Pi OS has it"
  fi
}

if [[ "$action" == reboot-off ]]; then
  remove_units trafficpi-reboot.timer "${reboot_units[@]}"
  run sudo systemctl daemon-reload
  echo "$removed ${reboot_units[*]}: the Pi is not rebooted every day any more"
  exit 0
fi

if [[ "$action" == backup-off ]]; then
  remove_units trafficpi-backup.timer "${backup_units[@]}"
  run sudo systemctl daemon-reload
  echo "$removed ${backup_units[*]}: no more daily backups (the ones already made stay where they are)"
  exit 0
fi

if [[ "$action" == link-watch-off ]]; then
  remove_link_watch
  run sudo systemctl daemon-reload
  echo "$removed ${link_units[*]}: nothing looks after the Wi-Fi link any more"
  exit 0
fi

remove_remote_conf() {
  if [[ -e "$remote_conf" ]]; then
    run sudo rm -f "$remote_conf"
    run sudo rmdir --ignore-fail-on-non-empty "$(dirname "$remote_conf")"
    run sudo systemctl daemon-reload
    echo "$removed $remote_conf: the API no longer believes a proxy about the client's address (restart trafficpi-api to apply)"
  fi
}

if [[ "$action" == trust-proxy-off ]]; then
  remove_remote_conf
  exit 0
fi

if [[ "$action" == journal-off ]]; then
  remove_journal_conf
  exit 0
fi

if [[ "$action" == remove ]]; then
  for unit in "${units[@]}"; do
    run sudo systemctl disable --now "$unit" 2>/dev/null || true
    run sudo rm -f "/etc/systemd/system/$unit"
  done
  remove_units trafficpi-reboot.timer "${reboot_units[@]}"
  remove_units trafficpi-backup.timer "${backup_units[@]}"
  remove_link_watch
  remove_remote_conf
  run sudo systemctl daemon-reload
  remove_journal_conf
  echo "$removed ${units[*]} ${reboot_units[*]} ${backup_units[*]} ${link_units[*]}"
  exit 0
fi

[[ -x "$root/backend/.venv/bin/trafficpi-vision" ]] \
  || { echo "no virtualenv in $root/backend: run 'uv sync' there first" >&2; exit 1; }
id "$user" >/dev/null
if (( dry )); then
  echo "(dry run: nothing is changed; lines that start with + are what would be run)"
else
  mkdir -p "$data_dir"
fi
if [[ -d "$data_dir" && "$(stat -c %U "$data_dir")" != "$user" ]]; then
  run sudo chown "$user" "$data_dir"
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

fill() { # fill SOURCE DEST: the unit with this checkout's user, folders and settings put in
  local backup_args="" backup_paths=""
  if [[ -n "$backup_to" ]]; then backup_args=" --to $backup_to"; backup_paths="$backup_to"; fi
  sed -e "s|@USER@|$user|g" -e "s|@INSTALL_DIR@|$root|g" -e "s|@DATA_DIR@|$data_dir|g" \
      -e "s|@REBOOT_AT@|$reboot_at|g" -e "s|@BACKUP_AT@|$backup_at|g" \
      -e "s|@BACKUP_KEEP@|$backup_keep|g" -e "s|@BACKUP_TO_ARGS@|$backup_args|g" \
      -e "s|@BACKUP_WRITE_PATHS@|$backup_paths|g" "$1" > "$2"
}

install_unit() { # install_unit NAME: render it, check it, and install it (or, in a dry run, diff it)
  local unit=$1
  fill "$root/deploy/systemd/$unit" "$tmp/$unit"
  systemd-analyze verify "$tmp/$unit"
  if (( dry )); then
    if [[ -e "/etc/systemd/system/$unit" ]]; then
      if diff -u "/etc/systemd/system/$unit" "$tmp/$unit" > "$tmp/$unit.diff"; then
        echo "$unit: the same as the installed one"
      else
        echo "$unit: differs from the installed one:"; sed 's/^/    /' "$tmp/$unit.diff"
      fi
    else
      echo "$unit: new:"; grep -v '^#' "$tmp/$unit" | sed 's/^/    /'
    fi
  fi
  run sudo install -m 644 "$tmp/$unit" "/etc/systemd/system/$unit"
}

for unit in "${units[@]}"; do
  install_unit "$unit"
done
if [[ -n "$reboot_at" ]]; then
  for unit in "${reboot_units[@]}"; do install_unit "$unit"; done
fi
if [[ -n "$backup_at" ]]; then
  for unit in "${backup_units[@]}"; do install_unit "$unit"; done
fi
if (( link_watch )); then
  run sudo install -m 755 "$root/scripts/link_watch.sh" "$link_script"
  for unit in "${link_units[@]}"; do install_unit "$unit"; done
fi
if (( trust_proxy )); then
  printf '%s\n' '# Written by scripts/install_services.sh --trust-proxy (docs/remote-access.md).' \
    '[Service]' 'Environment=TRAFFICPI_TRUST_PROXY_HEADERS=1' > "$tmp/10-remote.conf"
  run sudo install -D -m 644 "$tmp/10-remote.conf" "$remote_conf"
fi
run sudo systemctl daemon-reload
echo "$did ${units[*]} (user $user, data in $data_dir)"
(( ! trust_proxy )) || echo "$did $remote_conf: the API believes the client address that cloudflared passes on"
[[ -z "$reboot_at" ]] || echo "$did ${reboot_units[*]} (every day at $reboot_at, local time)"
[[ -z "$backup_at" ]] || echo "$did ${backup_units[*]} (every day at $backup_at, the newest $backup_keep kept in ${backup_to:-$data_dir/backups})"
(( ! link_watch )) || echo "$did ${link_units[*]} and $link_script (a look at the Wi-Fi link every minute)"

if (( journal )); then
  if cmp -s "$root/deploy/journald/50-trafficpi.conf" "$journal_conf" 2>/dev/null; then
    echo "$journal_conf is in place already: the journal survives reboots"
  else
    run sudo install -D -m 644 "$root/deploy/journald/50-trafficpi.conf" "$journal_conf"
    run sudo systemctl restart systemd-journald # the services' log streams are kept across this
    run sudo journalctl --flush                 # and what this boot has said so far moves to the disk
    echo "$did $journal_conf: the journal survives reboots now"
  fi
fi

if [[ "$action" == enable ]]; then
  run sudo systemctl enable --now "${units[@]}"
  if (( restart )); then
    run sudo systemctl restart "${units[@]}"
  fi
  echo "$started; logs: journalctl -u trafficpi-vision -u trafficpi-api -f"
  if [[ -n "$reboot_at" ]]; then
    run sudo systemctl enable trafficpi-reboot.timer
    run sudo systemctl restart trafficpi-reboot.timer # also picks up a new time if it was running
  fi
  if [[ -n "$backup_at" ]]; then
    run sudo systemctl enable trafficpi-backup.timer
    run sudo systemctl restart trafficpi-backup.timer
  fi
  if (( link_watch )); then
    run sudo systemctl enable trafficpi-link-watch.timer
    run sudo systemctl restart trafficpi-link-watch.timer
  fi
  if (( ! dry )) && { [[ -n "$reboot_at$backup_at" ]] || (( link_watch )); }; then
    systemctl list-timers 'trafficpi-*' --no-pager
  fi
fi
