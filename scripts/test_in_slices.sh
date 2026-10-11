#!/usr/bin/env bash
# Run the whole backend test suite in slices, so that the board never runs flat out for long.
#
#   scripts/test_in_slices.sh                 every test file, eight at a time
#   SLICE=4 COOL_BELOW=76 scripts/test_in_slices.sh
#   FIRST_SLICE=3 scripts/test_in_slices.sh   start at the third slice (after a failure, say)
#
# Why: this Pi is also the live camera. The whole suite in one go takes it to the 84 degrees where it
# slows itself down (the API tests with Argon2 do that alone), and on 2026-10-09 the board froze and was
# reset by its watchdog minutes after un-niced checks overlapped a stall of the camera (the
# cause is not proven). So each slice runs on two cores, at the lowest priority, behind the lock that
# keeps heavy jobs from overlapping, and only once the board is below COOL_BELOW degrees (it waits at
# most five minutes for that). The exit status is 0 when every slice passed.
set -u
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root/backend" || exit 2
size="${SLICE:-8}"
limit="${COOL_BELOW:-78}"
first="${FIRST_SLICE:-1}"
temperature() { vcgencmd measure_temp 2>/dev/null | sed -E 's/temp=([0-9]+)\..*/\1/' | grep -E '^[0-9]+$' || echo 0; }
files=(tests/test_*.py)
failed=0
for ((i = (first - 1) * size; i < ${#files[@]}; i += size)); do
  slice=("${files[@]:i:size}")
  waited=0
  while [ "$(temperature)" -ge "$limit" ] && [ "$waited" -lt 300 ]; do sleep 10; waited=$((waited + 10)); done
  echo "=== slice $((i / size + 1)) of $(((${#files[@]} + size - 1) / size)): $(basename -a "${slice[@]}" | tr '\n' ' ')(started at $(temperature) C after waiting ${waited} s)"
  taskset -c 2,3 nice -n 15 flock /tmp/trafficpi-heavy.lock uv run pytest -q -p no:cacheprovider "${slice[@]}" 2>&1 | tail -n 15
  status=${PIPESTATUS[0]}
  echo "--- slice exit status $status, now $(temperature) C"
  [ "$status" -eq 0 ] || failed=1
done
echo "ALL SLICES DONE, failed=$failed"
exit "$failed"
