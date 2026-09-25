#!/bin/sh
# Compile the unpublished shell against the test host's libweston 13 headers.
set -eu
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
output=$1
mkdir -p "$output"
protocol=/usr/share/wayland-protocols/unstable/text-input/text-input-unstable-v3.xml
wayland-scanner server-header "$protocol" "$output/text-input-v3-server.h"
wayland-scanner private-code "$protocol" "$output/text-input-v3-code.c"
cc -shared -fPIC -Wall -Wextra -Werror -g \
  "$source_dir/probe_shell.c" "$output/text-input-v3-code.c" -I"$output" \
  $(pkg-config --cflags --libs libweston-13 wayland-server) \
  -o "$output/probe-shell.so"
