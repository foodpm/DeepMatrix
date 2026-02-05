#!/usr/bin/env bash
set -euo pipefail

app_path="${1:?missing app_path}"
out_dmg="${2:?missing out_dmg}"
volname="${3:-DeepMatrix}"

tmp_dir="$(mktemp -d)"
app_name="$(basename "$app_path")"
ditto -rsrc "$app_path" "$tmp_dir/$app_name"
ln -s /Applications "$tmp_dir/Applications"
hdiutil create -volname "$volname" -srcfolder "$tmp_dir" -ov -format UDZO "$out_dmg"
rm -rf "$tmp_dir"
