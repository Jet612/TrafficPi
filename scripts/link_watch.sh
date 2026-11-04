#!/usr/bin/env bash
# Bring back a Wi-Fi link that stays lost. The Pi's Wi-Fi driver can wedge after a roam and then
# refuses every connection until it is restarted, and nothing else on the Pi notices. This looks
# once a minute (trafficpi-link-watch.timer), as root, and acts when the link has been gone for:
#
#   10 minutes   switches the radio off and on
#   15 minutes   reloads the driver
#   25 minutes   reboots the Pi, at most once in 6 hours
#
# A link is a connection to an access point, so the internet going down sets nothing off, or a
# default route over something else, such as a cable. The minutes are kept in /run, so they start
# again at each boot. Each step goes to the journal, and so does which one worked:
#
#   journalctl -u trafficpi-link-watch
#
# scripts/install_services.sh --link-watch copies this to /usr/local/sbin, where the user the
# services run as cannot change what root runs.
set -uo pipefail # no -e: a step that fails must not keep the next one from coming
shopt -s nullglob

iface=wlan0
driver=brcmfmac
run_dir="${RUNTIME_DIRECTORY:?start trafficpi-link-watch.service instead}"
state_dir="${STATE_DIRECTORY:?start trafficpi-link-watch.service instead}"
now="$(date +%s)"

has_link() {
  [[ "$(ip -o link show "$iface" 2>/dev/null)" == *" state UP "* ]] \
    || ip route show default 2>/dev/null | grep -qv " dev $iface "
}

switch_the_radio() {
  timeout 20 nmcli radio wifi off
  sleep 5
  timeout 20 nmcli radio wifi on
  sleep 5
  nmcli --wait 20 device connect "$iface" # a connection that failed is not tried again by itself
}

reload_the_driver() {
  local users=("/sys/module/$driver/holders/"*)
  modprobe -r "${users[@]##*/}" "$driver"
  modprobe "$driver"
}

reboot_unless_too_soon() {
  local last=0
  [[ ! -e "$state_dir/last-reboot" ]] || read -r last < "$state_dir/last-reboot"
  if (( now - ${last:-0} >= 6 * 3600 )); then
    echo "$now" > "$state_dir/last-reboot"
    touch "$state_dir/rebooted"
    echo "no Wi-Fi link for $down minutes: rebooting the Pi"
    systemctl reboot
  elif [[ "$step" != held-back ]]; then
    echo held-back > "$run_dir/step"
    echo "no Wi-Fi link for $down minutes, but the Pi was rebooted for it less than 6 hours ago: waiting"
  fi
}

if has_link; then
  if [[ -e "$run_dir/down-since" ]]; then
    read -r since < "$run_dir/down-since"
    read -r step < "$run_dir/step"
    case "$step" in
      radio) how="after the radio was switched off and on" ;;
      driver) how="after the driver was reloaded" ;;
      *) how="by itself" ;;
    esac
    echo "the Wi-Fi link is back after $(( (now - since) / 60 )) minutes, $how"
  elif [[ -e "$state_dir/rebooted" ]]; then
    echo "the Wi-Fi link is back after the reboot"
  fi
  rm -f "$run_dir/down-since" "$run_dir/step" "$state_dir/rebooted"
  exit 0
fi

if [[ ! -e "$run_dir/down-since" ]]; then
  echo "$now" > "$run_dir/down-since"
  echo none > "$run_dir/step"
  echo "no Wi-Fi link"
  exit 0
fi

read -r since < "$run_dir/down-since"
read -r step < "$run_dir/step"
down=$(( (now - since) / 60 ))

# Each step is written down before it is taken: one that hangs is ended by the unit's time limit,
# and the next look must go on to the next step and not try the same one again.
case "$step" in
  none)
    if (( down >= 10 )); then
      echo radio > "$run_dir/step"
      echo "no Wi-Fi link for $down minutes: switching the radio off and on"
      switch_the_radio
    fi
    ;;
  radio)
    if (( down >= 15 )); then
      echo driver > "$run_dir/step"
      echo "no Wi-Fi link for $down minutes: reloading the driver"
      reload_the_driver
    fi
    ;;
  *)
    (( down < 25 )) || reboot_unless_too_soon
    ;;
esac
