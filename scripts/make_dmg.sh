#!/usr/bin/env bash
set -euo pipefail

app_path="${1:?missing app_path}"
out_dmg="${2:?missing out_dmg}"
volname="${3:-DeepMatrix}"

tmp_dir="$(mktemp -d)"
app_name="$(basename "$app_path")"
mnt="$tmp_dir/mnt"
rw_dmg="$tmp_dir/rw.dmg"

retry() {
  local max="${RETRY_MAX:-6}"
  local delay="${RETRY_DELAY:-2}"
  local n=0
  until "$@"; do
    n=$((n + 1))
    if [[ $n -ge $max ]]; then
      return 1
    fi
    sleep "$delay"
  done
}

cleanup() {
  if mount | grep -q "on $mnt "; then
    hdiutil detach "$mnt" -force >/dev/null 2>&1 || true
  fi
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

mkdir -p "$mnt"

app_kb="$(du -sk "$app_path" | awk '{print $1}')"
size_mb="$(( (app_kb / 1024) + 200 ))"
if [[ $size_mb -lt 300 ]]; then
  size_mb=300
fi

retry hdiutil create -volname "$volname" -size "${size_mb}m" -fs HFS+ -type UDIF -ov "$rw_dmg"
retry hdiutil attach -readwrite -noverify -noautoopen -mountpoint "$mnt" "$rw_dmg" >/dev/null

ditto -rsrc "$app_path" "$mnt/$app_name"
ln -s /Applications "$mnt/Applications"
sync || true

retry hdiutil detach "$mnt" -force >/dev/null

rm -f "$out_dmg"
retry hdiutil convert "$rw_dmg" -format UDZO -imagekey zlib-level=9 -ov -o "$out_dmg" >/dev/null
