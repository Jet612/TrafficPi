#!/usr/bin/env bash
# Download yolov11s, the model the vision service uses by default, into <data dir>/models. The
# hailo-models package does not have it and this repository keeps no model files, so it comes from
# Hailo's Model Zoo: the build for HailoRT 4.23, checked against its checksum here, so that a changed
# download is refused. Without it the vision service says so and uses yolov8s from hailo-models.
#
#   scripts/fetch_model.sh                   for the accelerator in this machine
#   scripts/fetch_model.sh --data-dir DIR    (default: $TRAFFICPI_DATA_DIR, else backend/data)
#   scripts/fetch_model.sh --dry-run         say what would be done
#
# It is safe to run again: a file that is already there and correct is left as it is.
set -euo pipefail

ZOO=https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v2.17.0
MODEL=yolov11s
SHA256_HAILO8L=bba376de4e88dbb874e3787a6143650c2a8f4d6f3300f355b877b490480e9460
SHA256_HAILO8=8a500ccc9e2797295dc0cb0c41cd69ae1d728f2190dc51fdfdf8345a4f7fa036

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
data_dir="${TRAFFICPI_DATA_DIR:-$root/backend/data}"
dry=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --data-dir) data_dir="${2:?--data-dir wants a folder}"; shift ;;
    --dry-run) dry=1 ;;
    -h|--help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

architecture="$(hailortcli fw-control identify 2>/dev/null | sed -n 's/^Device Architecture: *//p' | tr -d '[:space:]')"
case "$architecture" in
  HAILO8L) folder=hailo8l; suffix=h8l; sha256="$SHA256_HAILO8L" ;;
  HAILO8) folder=hailo8; suffix=h8; sha256="$SHA256_HAILO8" ;;
  *) echo "cannot tell which accelerator this is (got '$architecture'): is the driver loaded?" >&2; exit 1 ;;
esac

target="$data_dir/models/${MODEL}_$suffix.hef"
url="$ZOO/$folder/$MODEL.hef"
if [[ -f "$target" ]] && echo "$sha256  $target" | sha256sum --check --status; then
  echo "$target is in place"
  exit 0
fi
if (( dry )); then
  echo "would download $url to $target"
  exit 0
fi

mkdir -p "$data_dir/models"
partial="$target.part"
trap 'rm -f "$partial"' EXIT
curl -fsSL --retry 3 --connect-timeout 20 -o "$partial" "$url"
if ! echo "$sha256  $partial" | sha256sum --check --status; then
  echo "the download from $url does not match its checksum: refused" >&2
  exit 1
fi
mv "$partial" "$target"
echo "$target is in place"
