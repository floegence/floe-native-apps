#!/bin/sh
# Acquire original signed archives in the disposable native builder only.
# This creates a candidate manifest; it never activates an installed component.
set -eu
output=$1
architecture=$(apk --print-arch)
case "$architecture" in x86_64) go_architecture=amd64 ;; aarch64) go_architecture=arm64 ;; *) exit 1 ;; esac
test ! -e "$output"
mkdir -p "$output/apks"
set -- weston=14.0.2-r4 weston-backend-headless=14.0.2-r4 weston-xwayland=14.0.2-r4 \
    xwayland xauth xkeyboard-config xdg-desktop-portal=1.20.3-r4 \
    xdg-desktop-portal-gtk=1.15.3-r1 ibus=1.5.33-r0 py3-gobject3 py3-xlib xcb-imdkit
apk update
apk fetch --recursive --url "$@" > "$output/urls.txt"
apk fetch --recursive --output "$output/apks" "$@"
apk verify "$output"/apks/*.apk > "$output/signatures.txt"
cp /etc/apk/repositories "$output/repositories.txt"
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 "$source_dir/candidate_catalog.py" "$output" "$go_architecture"
