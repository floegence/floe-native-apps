#!/bin/sh
# Compile in the disposable native qualification system, never on a user host.
set -eu
source_dir=$1
output=$2
major=${3:-6}
: "${FLOE_DESKTOP_QT_BUILDER_IMAGE:?Set the immutable native Qt builder identity}"
case "$major" in 5|6) ;; *) exit 1 ;; esac
test "${FLOE_QT_MAJOR:-}" = "$major"
test -f /native-builder-archives.sha256
case "$source_dir:$output" in /*:/*) ;; *) exit 1 ;; esac
test ! -e "$output"
export SOURCE_DATE_EPOCH=0
mkdir -p "$output/platforminputcontexts"
version=$(pkg-config --modversion "Qt${major}Gui")
includes=$(pkg-config --variable=includedir "Qt${major}Gui")
if [ "$major" = 5 ]; then
    moc=$(pkg-config --variable=host_bins Qt5Core)/moc
else
    moc=$(pkg-config --variable=libexecdir Qt6Core)/moc
fi
"$moc" $(pkg-config --cflags "Qt${major}Gui" "Qt${major}DBus") \
    -I"$includes/QtGui/$version/QtGui" "$source_dir/qt_native.cpp" -o "$output/qt_native.moc"
c++ -shared -fPIC -fvisibility=hidden -Wall -Wextra -Werror -O2 -std=c++17 \
    -ffile-prefix-map="$source_dir"=. -ffile-prefix-map="$output"=. \
    -I"$output" -I"$includes/QtGui/$version" -I"$includes/QtGui/$version/QtGui" \
    -I"$includes/QtCore/$version" -I"$includes/QtCore/$version/QtCore" \
    $(pkg-config --cflags "Qt${major}Gui" "Qt${major}DBus" wayland-client) "$source_dir/qt_native.cpp" \
    $(pkg-config --libs "Qt${major}Gui" "Qt${major}DBus" wayland-client) -Wl,-z,relro,-z,now \
    -o "$output/platforminputcontexts/libfloe-client-native.so"
strip --strip-unneeded "$output/platforminputcontexts/libfloe-client-native.so"
python3 - "$source_dir" "$output" "$major" "$0" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

source, output, major, script = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]).resolve()
binary = output / 'platforminputcontexts/libfloe-client-native.so'
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
elf = subprocess.check_output(['readelf', '-hldV', str(binary)], text=True)
if '(RPATH)' in elf or '(RUNPATH)' in elf:
    raise ValueError('Private Qt module must resolve only its application runtime')
record = {
    'architecture': platform.machine(), 'builder_image': os.environ['FLOE_DESKTOP_QT_BUILDER_IMAGE'],
    'snapshot': '20250224T000000Z',
    'qt': subprocess.check_output(['pkg-config', '--modversion', 'Qt' + major + 'Gui'], text=True).strip(),
    'compiler': subprocess.check_output(['c++', '--version'], text=True).splitlines()[0],
    'source_sha256': {str(path.name): digest(path) for path in
        (source / 'qt_native.cpp', source / 'qt_native.json', script, script.parent / 'Dockerfile.qt-native')},
    'signed_builder_archives': Path('/native-builder-archives.sha256').read_text().splitlines(),
    'packages': subprocess.check_output(['dpkg-query', '-W', '-f=${Package}=${Version}\n'], text=True).splitlines(),
    'artifact': {'sha256': digest(binary), 'elf': elf},
    'status': 'unpublished native ABI candidate; actual application qualification required',
}
(output / 'build.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
PY
