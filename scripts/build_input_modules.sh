#!/bin/sh
# Build first-party adapters in a disposable native Linux build environment.
set -eu
cd "$(dirname "$0")/.."
output=${1:?Usage: build_input_modules.sh ABSOLUTE_OUTPUT_DIRECTORY [5|6]}
major=${2:-5}
: "${FLOE_INPUT_BUILDER_IMAGE:?Set the original pinned Debian builder image digest}"
case "$output" in /*) ;; *) exit 2 ;; esac
case "$major" in 5|6) ;; *) exit 2 ;; esac
mkdir -p "$output"
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT HUP INT TERM
export SOURCE_DATE_EPOCH=0
qt=Qt${major}
version=$(pkg-config --modversion "${qt}Gui")
includes=$(pkg-config --variable=includedir "${qt}Gui")
if [ "$major" = 5 ]; then
    moc=$(pkg-config --variable=host_bins Qt5Core)/moc
else
    moc=$(pkg-config --variable=libexecdir Qt6Core)/moc
fi
"$moc" $(pkg-config --cflags "${qt}Gui" "${qt}DBus") \
    -I"$includes/QtGui/$version/QtGui" input_modules/qt.cpp -o "$temporary/qt.moc"
c++ -shared -fPIC -fvisibility=hidden -O2 -std=c++17 \
    -ffile-prefix-map="$PWD"=. -ffile-prefix-map="$temporary"=. \
    -I"$temporary" -I"$includes/QtGui/$version" -I"$includes/QtGui/$version/QtGui" \
    -I"$includes/QtCore/$version" -I"$includes/QtCore/$version/QtCore" \
    $(pkg-config --cflags "${qt}Gui" "${qt}DBus") input_modules/qt.cpp \
    $(pkg-config --libs "${qt}Gui" "${qt}DBus") -Wl,-z,relro,-z,now \
    -o "$output/libfloe-qt$major.so"
strip --strip-unneeded "$output/libfloe-qt$major.so"
if [ "$major" = 5 ]; then
    cc -shared -fPIC -fvisibility=hidden -O2 -ffile-prefix-map="$PWD"=. \
        $(pkg-config --cflags gtk+-3.0 gmodule-2.0) input_modules/gtk3.c \
        $(pkg-config --libs gtk+-3.0 gmodule-2.0) -Wl,-z,relro,-z,now \
        -o "$output/libfloe-gtk3.so"
    strip --strip-unneeded "$output/libfloe-gtk3.so"
fi
python3 - "$output" "$major" <<'PY'
import hashlib
import json
import os
import platform
from pathlib import Path
import subprocess
import sys

output, major = Path(sys.argv[1]), sys.argv[2]
names = [f'libfloe-qt{major}.so'] + (['libfloe-gtk3.so'] if major == '5' else [])
sources = ['input_modules/qt.cpp', 'input_modules/qt.json', 'scripts/build_input_modules.sh']
if major == '5':
    sources.append('input_modules/gtk3.c')
record = {
    'architecture': platform.machine(),
    'builder_image': os.environ['FLOE_INPUT_BUILDER_IMAGE'],
    'snapshot': '20250224T000000Z',
    'compiler': subprocess.check_output(['c++', '--version'], text=True).splitlines()[0],
    'packages': subprocess.check_output(['dpkg-query', '-W', '-f=${Package}=${Version}\n'], text=True).splitlines(),
    'source_sha256': {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in sources},
    'artifacts': {p: hashlib.sha256((output / p).read_bytes()).hexdigest() for p in names},
}
(output / f'build-qt{major}.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
PY
