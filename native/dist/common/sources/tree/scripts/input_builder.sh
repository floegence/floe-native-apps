#!/bin/sh
# Disposable Debian build fixture; never run on an application host.
set -eu
test -f /.dockerenv || test -f /tmp/floe-input-build-fixture
major=${1:-5}
case "$major" in 5) suite=bullseye; packages='libgtk-3-dev qtbase5-private-dev meson ninja-build curl libgraphene-1.0-dev libxkbcommon-dev' ;;
  6) suite=bookworm; packages='qt6-base-private-dev' ;; *) exit 2 ;; esac
rm -f /etc/apt/sources.list.d/*
cat > /etc/apt/sources.list <<EOF
deb [check-valid-until=no] https://snapshot-cloudflare.debian.org/archive/debian/20250224T000000Z/ $suite main
deb [check-valid-until=no] https://snapshot-cloudflare.debian.org/archive/debian-security/20250224T000000Z/ $suite-security main
EOF
apt-get update
apt-get install -y --no-install-recommends build-essential pkg-config python3 $packages
