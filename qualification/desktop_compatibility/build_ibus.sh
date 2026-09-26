#!/bin/sh
# Native disposable source qualification only; install no host components.
set -eu
archive=$1
source_patch=$2
output=$3
case "$archive:$source_patch:$output" in /*:/*:/*) ;; *) exit 1 ;; esac
test ! -e "$output"
test "$(sha512sum "$archive" | cut -d ' ' -f 1)" = 33de8c607c4bdcbf1ef3b596151008b33216f3f37e720de0c378f2730099464da800d95dd9bcded10752d689f3480f111962eab507539e239113f0f422053c63
mkdir -p "$output"
tar --no-same-owner -xzf "$archive" -C "$output"
cd "$output/ibus-1.5.33"
patch --batch --fuzz=0 -p1 < "$source_patch"
./configure --prefix=/usr --libexecdir=/usr/lib/ibus --disable-gtk2 --disable-gtk3 \
    --disable-gtk4 --disable-xim --disable-ui --disable-wayland --disable-engine \
    --disable-emoji-dict --disable-unicode-dict --disable-tests --disable-introspection \
    --disable-vala --disable-gtk-doc --disable-dconf --disable-python2 --disable-setup \
    --disable-systemd-services --disable-surrounding-text --disable-nls \
    --disable-appindicator --disable-libnotify
make -j2 -C src
make -j2 -C bus
# The release tarball's generated portal was produced by a newer GLib. Derive
# the unchanged XML with the actual build baseline instead of importing its ABI.
(cd portal && gdbus-codegen --interface-prefix org.freedesktop.IBus. \
    --c-namespace IBusDbus --generate-c-code ibus-portal-dbus org.freedesktop.IBus.Portal.xml)
make -j2 -C portal
sha256sum "$archive" "$source_patch" bus/inputcontext.c bus/ibusimpl.c portal/portal.c \
    portal/ibus-portal-dbus.c \
    bus/.libs/ibus-daemon portal/.libs/ibus-portal src/.libs/libibus-1.0.so.5.0.533 > "$output/digests.txt"
cc --version > "$output/compiler.txt"
pkg-config --modversion glib-2.0 gio-2.0 dbus-1 > "$output/versions.txt"
