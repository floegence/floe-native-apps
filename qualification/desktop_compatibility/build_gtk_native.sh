#!/bin/sh
# Compile first-party adapters on the matching native Debian 11 fixture.
set -eu
repository=$1
output=$2
: "${FLOE_DESKTOP_GTK_BUILDER_IMAGE:?Set the immutable native GTK builder identity}"
test -f /native-builder-archives.sha256
case "$repository:$output" in /*:/*) ;; *) exit 1 ;; esac
test ! -e "$output"
mkdir -p "$output"
export SOURCE_DATE_EPOCH=0
export PKG_CONFIG_PATH=/opt/gtk4/lib/pkgconfig
export LD_LIBRARY_PATH=/opt/gtk4/lib
for major in 3 4; do
    package=gtk+-3.0
    if [ "$major" = 4 ]; then package=gtk4-x11; fi
    cc -shared -fPIC -fvisibility=hidden -Wall -Wextra -Werror -O2 \
        -ffile-prefix-map="$repository"=. \
        $(pkg-config --cflags "$package" gmodule-2.0) "$repository/native/gtk_native.c" \
        $(pkg-config --libs "$package" gmodule-2.0) -ldl -Wl,-z,relro,-z,now \
        -o "$output/libfloe-gtk$major-native.so"
    strip --strip-unneeded "$output/libfloe-gtk$major-native.so"
done
python3 - "$repository" "$output" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

root, output = map(Path, sys.argv[1:])
sources = ['native/gtk_native.c', 'qualification/desktop_compatibility/build_gtk_native.sh',
    'qualification/desktop_compatibility/Dockerfile.gtk-native', 'scripts/input_builder.sh',
    'scripts/input_gtk_builder.sh', 'scripts/input_toolkits.json']
record = {
    'architecture': platform.machine(), 'builder_image': os.environ['FLOE_DESKTOP_GTK_BUILDER_IMAGE'],
    'snapshot': '20250224T000000Z', 'toolkit_sources': json.loads((root / 'scripts/input_toolkits.json').read_text()),
    'compiler': subprocess.check_output(['cc', '--version'], text=True).splitlines()[0],
    'source_sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sources},
    'signed_builder_archives': Path('/native-builder-archives.sha256').read_text().splitlines(),
    'packages': subprocess.check_output(['dpkg-query', '-W', '-f=${Package}=${Version}\n'], text=True).splitlines(),
    'artifacts': {}, 'toolkits': {},
}
for major in (3, 4):
    binary = output / f'libfloe-gtk{major}-native.so'
    elf = subprocess.check_output(['readelf', '-hldV', str(binary)], text=True)
    if '(RPATH)' in elf or '(RUNPATH)' in elf:
        raise ValueError('Native GTK module must resolve the application toolkit')
    if any(tuple(map(int, version.split('.'))) > (2, 31) for version in re.findall(r'GLIBC_(\d+\.\d+)', elf)):
        raise ValueError('Native GTK module exceeds the glibc 2.31 baseline')
    record['artifacts'][binary.name] = {'sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'elf': elf}
    record['toolkits'][str(major)] = subprocess.check_output(
        ['pkg-config', '--modversion', 'gtk+-3.0' if major == 3 else 'gtk4-x11'], text=True).strip()
(output / 'build.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
PY
