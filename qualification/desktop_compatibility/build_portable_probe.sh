#!/bin/sh
# Run only inside the disposable native Alpine 3.23 builder. Package acquisition
# and signature verification are separate; this script installs nothing.
set -eu
: "${FLOE_DESKTOP_BUILDER_IMAGE:?Set the immutable native builder image identity}"
test -f /builder-apks.sha256
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
output=$1
capture_protocol=$2
test -n "${FLOE_PROBE_WESTON_SOURCE:-}"
test -f "$FLOE_PROBE_WESTON_SOURCE/include/libweston/xwayland-api.h"
test "$(pkg-config --modversion libweston-14)" = 14.0.2
test "$(sha256sum "$capture_protocol" | cut -d ' ' -f 1)" = 8cedbfc3eca413505b7ea8bd99d5f05f3d0724e311f159186070d1f8c107bc10
mkdir -p "$output"
protocol=/usr/share/wayland-protocols/unstable/text-input/text-input-unstable-v3.xml
wayland-scanner server-header "$protocol" "$output/text-input-v3-server.h"
wayland-scanner private-code "$protocol" "$output/text-input-v3-code.c"
# Treat publisher headers as system headers; warnings in our source still fail.
cc -shared -fPIC -Wall -Wextra -Werror -g \
  "$source_dir/probe_shell.c" "$output/text-input-v3-code.c" -I"$output" -isystem "$FLOE_PROBE_WESTON_SOURCE/include" \
  $(pkg-config --cflags libweston-14 wayland-server | sed 's/-I/-isystem /g') \
  $(pkg-config --libs libweston-14 wayland-server) -lm -o "$output/probe-shell.so"
wayland-scanner client-header "$capture_protocol" "$output/weston-output-capture-client.h"
wayland-scanner private-code "$capture_protocol" "$output/weston-output-capture-code.c"
cc -Wall -Wextra -Werror -O2 "$source_dir/frame_probe.c" \
  "$output/weston-output-capture-code.c" -I"$output" \
  $(pkg-config --cflags --libs wayland-client libdrm libpng) -o "$output/frame-probe"
pkg-config --modversion libweston-14 wayland-server libpng > "$output/versions.txt"
cc --version > "$output/compiler.txt"
sha256sum "$source_dir/probe_shell.c" "$source_dir/frame_probe.c" "$protocol" \
  "$capture_protocol" "$output/probe-shell.so" "$output/frame-probe" > "$output/digests.txt"
python3 - "$output" "$source_dir" <<'PYTHON'
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
output, source = map(Path, sys.argv[1:])
record = {'architecture': platform.machine(), 'builder_image': os.environ['FLOE_DESKTOP_BUILDER_IMAGE'],
    'signed_builder_archives': Path('/builder-apks.sha256').read_text().splitlines(),
    'packages': subprocess.check_output(['apk', 'info', '-vv'], text=True).splitlines(),
    'source': {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in
               ('probe_shell.c', 'frame_probe.c', 'build_portable_probe.sh', 'Dockerfile.desktop-native')},
    'artifacts': {name: {'sha256': hashlib.sha256((output / name).read_bytes()).hexdigest(),
        'elf': subprocess.check_output(['readelf', '-hld', str(output / name)], text=True)}
        for name in ('probe-shell.so', 'frame-probe')},
    'status': 'unactivated native artifacts; complete installed-stack qualification required'}
(output / 'build.json').write_text(json.dumps(record, indent=2) + '\n')
PYTHON
