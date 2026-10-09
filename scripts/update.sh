#!/usr/bin/env bash
# Bring a running TrafficPi up to date with this checkout: the locked Python packages, the
# dashboard, the systemd units, and a restart of the services. Run it from the checkout, as the user
# who installed TrafficPi.
#
#   scripts/update.sh              update what is checked out
#   scripts/update.sh --pull       first `git pull --ff-only` (it refuses if you have local changes)
#   scripts/update.sh --no-restart do everything except restart the services (restart them later:
#                                  sudo systemctl restart trafficpi-vision trafficpi-api)
#   scripts/update.sh --dry-run    say what would be done
#
# Restarting the vision service stops the counting for about ten seconds. Database changes are made
# by the services when they start. The extras that scripts/install_services.sh installed (the daily
# reboot, the nightly backup, the persistent journal, the proxy setting) are read back from the
# installed units and put in again, so that an update does not lose them.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dry=0
pull=0
restart=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) dry=1 ;;
    --pull) pull=1 ;;
    --no-restart) restart=0 ;;
    -h|--help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

export PATH="$HOME/.local/bin:$PATH"
units=/etc/systemd/system
say() { printf '\n== %s\n' "$1"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
run() {
  if (( dry )); then printf '   + %s\n' "$*"; else "$@"; fi
}

[[ -f "$units/trafficpi-vision.service" ]] \
  || die "the services are not installed: run scripts/install.sh first"
(( EUID != 0 )) || die "run this as your own user, not as root"

say "Where it is now"
git -C "$root" log --oneline -1

if (( pull )); then
  say "Fetching the newest version"
  [[ -z "$(git -C "$root" status --porcelain --untracked-files=no)" ]] \
    || die "there are local changes in $root; commit or stash them first"
  run git -C "$root" pull --ff-only
fi

say "The locked Python packages"
if (( dry )); then printf '   + (cd backend && uv sync --locked)\n'; else (cd "$root/backend" && uv sync --locked); fi

say "The dashboard"
tree=""
stamp="$root/dashboard/out/.built-from"
if [[ -z "$(git -C "$root" status --porcelain -- dashboard)" ]]; then
  tree="$(git -C "$root" rev-parse HEAD:dashboard)"
fi
if [[ -n "$tree" && -f "$stamp" && "$(cat "$stamp")" == "$tree" ]]; then
  echo "   already built from this version"
elif (( dry )); then
  printf '   + (cd dashboard && pnpm install --frozen-lockfile && pnpm build)\n'
else
  (cd "$root/dashboard" && pnpm install --frozen-lockfile && pnpm build)
  [[ -z "$tree" ]] || echo "$tree" > "$stamp"
fi

# What is installed now, as the options that would install it again.
installed_options() {
  local service="$units/trafficpi-vision.service" value
  value="$(sed -n 's/^User=//p' "$service" | head -1)"; options+=(--user "$value")
  value="$(sed -n 's/^Environment=TRAFFICPI_DATA_DIR=//p' "$service" | head -1)"; options+=(--data-dir "$value")
  if [[ -f "$units/trafficpi-reboot.timer" ]]; then
    value="$(sed -n 's/^OnCalendar=\*-\*-\* \([0-9:]*\):00$/\1/p' "$units/trafficpi-reboot.timer" | head -1)"
    options+=(--reboot-at "${value:0:5}")
  fi
  if [[ -f "$units/trafficpi-backup.timer" ]]; then
    value="$(sed -n 's/^OnCalendar=\*-\*-\* \([0-9:]*\):00$/\1/p' "$units/trafficpi-backup.timer" | head -1)"
    options+=(--backup-at "${value:0:5}")
    local command; command="$(sed -n 's/^ExecStart=//p' "$units/trafficpi-backup.service" | head -1)"
    [[ "$command" =~ --keep[[:space:]]+([0-9]+) ]] && options+=(--backup-keep "${BASH_REMATCH[1]}")
    [[ "$command" =~ --to[[:space:]]+([^[:space:]]+) ]] && options+=(--backup-to "${BASH_REMATCH[1]}")
  fi
  [[ ! -f /etc/systemd/journald.conf.d/50-trafficpi.conf ]] || options+=(--persistent-journal)
  [[ ! -f "$units/trafficpi-api.service.d/10-remote.conf" ]] || options+=(--trust-proxy)
}

say "The systemd units"
options=(--enable)
installed_options
(( ! restart )) || options+=(--restart)
(( ! dry )) || options+=(--dry-run)
echo "   scripts/install_services.sh ${options[*]}"
"$root/scripts/install_services.sh" "${options[@]}"

if (( ! dry && restart )); then
  say "Waiting for the services"
  for _ in $(seq 1 45); do
    if curl -fsS --max-time 3 http://127.0.0.1:8000/api/health >/dev/null 2>&1 \
      && curl -fsS --max-time 3 http://127.0.0.1:8001/health >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
  sudo -n true 2>/dev/null || sudo -v # the checks use sudo; ask only if it would ask
  "$root/scripts/check_boot.sh"
elif (( ! dry )); then
  printf '\nNot restarted: the running services are still the old ones. When it suits:\n  sudo systemctl restart trafficpi-vision trafficpi-api\n'
fi
