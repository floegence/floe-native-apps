#!/bin/sh
# Run only inside the disposable native Alpine 3.23 builder. Package acquisition
# and signature verification are separate; this script installs nothing.
set -eu
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
output=$1
test "$(pkg-config --modversion libweston-14)" = 14.0.2
mkdir -p "$output"
protocol=/usr/share/wayland-protocols/unstable/text-input/text-input-unstable-v3.xml
wayland-scanner server-header "$protocol" "$output/text-input-v3-server.h"
wayland-scanner private-code "$protocol" "$output/text-input-v3-code.c"
# Treat publisher headers as system headers; warnings in our source still fail.
cc -shared -fPIC -Wall -Wextra -Werror -g \
  "$source_dir/probe_shell.c" "$output/text-input-v3-code.c" -I"$output" \
  $(pkg-config --cflags libweston-14 wayland-server | sed 's/-I/-isystem /g') \
  $(pkg-config --libs libweston-14 wayland-server) -o "$output/probe-shell.so"
pkg-config --modversion libweston-14 wayland-server > "$output/versions.txt"
cc --version > "$output/compiler.txt"
sha256sum "$source_dir/probe_shell.c" "$protocol" "$output/probe-shell.so" > "$output/digests.txt"
