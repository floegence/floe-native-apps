#!/bin/sh
# Native disposable builder only. Produce unactivated, auditable component files.
set -eu
source_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
weston_archive=$1
ibus_archive=$2
output=$3
: "${FLOE_DESKTOP_BUILDER_IMAGE:?Set the immutable native builder image identity}"
test ! -e "$output"
mkdir -p "$output"
export SOURCE_DATE_EPOCH=0
fixture=$source_root/qualification/desktop_compatibility
sh "$fixture/build_weston.sh" "$weston_archive" \
    "$source_root/native/patches/weston-14-input-events.patch" "$output/weston"
DESTDIR="$output/weston/install" meson install --no-rebuild -C "$output/weston/build"
export FLOE_PROBE_WESTON_SOURCE="$output/weston/weston-14.0.2"
sh "$fixture/build_portable_probe.sh" "$output/shell" \
    "$FLOE_PROBE_WESTON_SOURCE/protocol/weston-output-capture.xml"
sh "$fixture/build_portable_ibus.sh" "$ibus_archive" \
    "$source_root/native/patches/ibus-1.5-context-source.patch" "$output/ibus"
python3 - "$source_root" "$weston_archive" "$ibus_archive" "$output" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

source, weston, ibus, output = map(Path, sys.argv[1:])
architecture = {'x86_64': 'amd64', 'aarch64': 'arm64'}[platform.machine()]
artifacts = output / 'artifacts'
artifacts.mkdir()
files = {
    'desktop-shell.so': output / 'shell/probe-shell.so',
    'desktop-capture': output / 'shell/frame-probe',
    'libweston-14.so.0': output / 'weston/install/usr/lib/libweston-14.so.0.0.2',
    'xwayland.so': output / 'weston/install/usr/lib/libweston-14/xwayland.so',
    'ibus-daemon': output / 'ibus/artifacts/ibus-daemon',
    'ibus-portal': output / 'ibus/artifacts/ibus-portal',
    'libibus-1.0.so.5': output / 'ibus/artifacts/libibus-1.0.so.5.0.533',
}
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
manifest = {}
for name, original in files.items():
    target = artifacts / name
    shutil.copyfile(original, target)
    target.chmod(0o755)
    subprocess.run(['strip', '--strip-unneeded', str(target)], check=True)
    elf = subprocess.check_output(['readelf', '-hld', str(target)], text=True)
    # Build paths must not remain in installed loader search paths.
    if '(RPATH)' in elf or '(RUNPATH)' in elf:
        raise ValueError('Native component contains an implicit loader path: ' + name)
    expected_machine = 'AArch64' if architecture == 'arm64' else 'Advanced Micro Devices X86-64'
    if expected_machine not in elf:
        raise ValueError('Component is not built for the executing native architecture')
    manifest[name] = {'sha256': digest(target), 'size_bytes': target.stat().st_size, 'elf': elf}
licenses = output / 'licenses'
licenses.mkdir()
for name, path in {'weston-COPYING': output / 'weston/weston-14.0.2/COPYING',
                   'ibus-COPYING': output / 'ibus/ibus-1.5.33/COPYING'}.items():
    shutil.copyfile(path, licenses / name)
paths = ['scripts/build_desktop_native.sh', 'qualification/desktop_compatibility/Dockerfile.desktop-native',
    'qualification/desktop_compatibility/build_weston.sh',
    'qualification/desktop_compatibility/build_portable_probe.sh',
    'qualification/desktop_compatibility/build_portable_ibus.sh',
    'qualification/desktop_compatibility/build_ibus.sh',
    'qualification/desktop_compatibility/probe_shell.c',
    'qualification/desktop_compatibility/clipboard_native.h',
    'qualification/desktop_compatibility/frame_probe.c',
    'native/patches/weston-14-input-events.patch', 'native/patches/ibus-1.5-context-source.patch']
record = {'version': 1, 'architecture': architecture,
    'builder_image': os.environ['FLOE_DESKTOP_BUILDER_IMAGE'],
    'compiler': subprocess.check_output(['cc', '--version'], text=True).splitlines()[0],
    'signed_builder_archives': Path('/builder-apks.sha256').read_text().splitlines(),
    'packages': subprocess.check_output(['apk', 'info', '-vv'], text=True).splitlines(),
    'original_sources': {path.name: digest(path) for path in (weston, ibus)},
    'sources': {name: digest(source / name) for name in paths},
    'licenses': {path.name: digest(path) for path in licenses.iterdir()},
    'artifacts': manifest,
    'status': 'unactivated native build; installation and application qualification are required'}
(output / 'build.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
PY
