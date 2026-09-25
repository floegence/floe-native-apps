#!/bin/sh
# Disposable native Alpine builder only. Keep publisher archives immutable and
# record the exact derived source/library used by the private compositor.
set -eu
archive=$1
patch_file=$2
destination=$3
test "$(sha512sum "$archive" | cut -d ' ' -f 1)" = e8214ec893e6c3ae94eb3c92feba104b0201843e9143f726a3e9a4d396d02523c94da706c1348cf934bc339fb1a4bc1fecdb865f0ea914115fd346d9eda091f5
test ! -e "$destination"
mkdir -p "$destination"
tar -xJf "$archive" -C "$destination"
source_root=$destination/weston-14.0.2
patch -d "$source_root" -p1 --fuzz=0 < "$patch_file"
meson setup "$destination/build" "$source_root" --buildtype=release \
    -Dbackend-default=headless -Dbackend-headless=true -Dxwayland=true \
    -Dbackend-drm=false -Dbackend-drm-screencast-vaapi=false \
    -Dbackend-pipewire=false -Dbackend-rdp=false -Dbackend-vnc=false \
    -Dbackend-wayland=false -Dbackend-x11=false -Drenderer-gl=false \
    -Dscreenshare=false -Dsystemd=false -Dremoting=false -Dpipewire=false \
    -Dshell-desktop=false -Dshell-fullscreen=false -Dshell-ivi=false -Dshell-kiosk=false \
    -Dcolor-management-lcms=false -Dimage-jpeg=false -Dimage-webp=false \
    -Dtools= -Ddemo-clients=false -Dsimple-clients= -Dwcap-decode=false -Dtests=false -Ddoc=false
ninja -C "$destination/build"
sha256sum "$archive" "$patch_file" "$source_root/COPYING" \
    "$source_root/include/libweston/libweston.h" "$source_root/libweston/input.c" \
    "$source_root/libweston/desktop/seat.c" \
    "$destination/build/libweston/libweston-14.so.0.0.2" > "$destination/digests.txt"
apk info -vv > "$destination/build-packages.txt"
cc --version > "$destination/compiler.txt"
