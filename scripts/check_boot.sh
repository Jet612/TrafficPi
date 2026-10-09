#!/usr/bin/env bash
# Checks the accelerator and camera stack, for example right after a reboot (M0's cold-boot check).
# Prints PASS or FAIL per check and exits non-zero if anything failed.
set -u
export HAILORT_LOGGER_PATH=NONE  # HailoRT would otherwise write hailort.log into the current directory
here="$(cd "$(dirname "$0")" && pwd)"
passed=0; failed=0

check() {
  local name=$1; shift
  local out
  if out=$("$@" 2>&1); then
    printf 'PASS  %s\n' "$name"; passed=$((passed + 1))
  else
    printf 'FAIL  %s\n' "$name"; printf '        %s\n' "$(printf '%s' "$out" | grep -v -E ' INFO | WARN ' | tail -3)"; failed=$((failed + 1))
  fi
}

check "hailo_pci kernel module is loaded"          bash -c 'lsmod | grep -q "^hailo_pci"'
check "DKMS driver is installed for this kernel"   bash -c 'dkms status | grep -q "hailo_pci.*$(uname -r).*installed"'
check "/dev/hailo0 exists"                         test -c /dev/hailo0
check "accelerator reports HAILO8L (or HAILO8)"    bash -c 'hailortcli fw-control identify | grep -E "Device Architecture: HAILO8L?"'
check "no Hailo errors in the kernel log"          bash -c '! sudo -n dmesg | grep -i hailo | grep -i -E "error|fail|timeout"'
check "bootloader is up to date"                   bash -c 'sudo -n rpi-eeprom-update | grep -q "BOOTLOADER: up to date" || { sudo -n rpi-eeprom-update | grep BOOTLOADER; exit 1; }'
check "camera is detected"                         bash -c 'rpicam-hello --list-cameras 2>&1 | grep -q imx708'
check "PCIe link to the accelerator is Gen 3"      bash -c 'sudo -n lspci -vv -s "$(lspci | awk "/Hailo/ {print \$1; exit}")" | grep -q "LnkSta:.*8GT/s"'
check "smoke test: camera + accelerator for 10 s"  python3 -I "$here/smoke_hailo.py" --seconds 10

printf '\n%d passed, %d failed\n' "$passed" "$failed"
[ "$failed" -eq 0 ]
