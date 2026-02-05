#!/usr/bin/env bash
set -euo pipefail

app_path="${1:?missing app_path}"
out_dmg="${2:?missing out_dmg}"
volname="${3:-DeepMatrix}"

tmp_dir="$(mktemp -d)"
cp -R "$app_path" "$tmp_dir/"
hdiutil create -volname "$volname" -srcfolder "$tmp_dir" -ov -format UDZO "$out_dmg"
rm -rf "$tmp_dir"
