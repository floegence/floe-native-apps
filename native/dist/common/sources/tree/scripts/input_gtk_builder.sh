#!/bin/sh
# Original toolkit sources are build/test fixtures only, never module payloads.
set -eu
[ "$1" = 5 ] || exit 0
python3 - <<'PYTHON'
import hashlib
import json
from pathlib import Path
import subprocess
records = json.loads(Path('/tmp/input_toolkits.json').read_text())
for name, record in records.items():
    archive = Path('/tmp/sources') / (name + '-' + record['version'] + '.tar.xz')
    if hashlib.sha256(archive.read_bytes()).hexdigest() != record['sha256']:
        raise RuntimeError('Original toolkit archive integrity failure: ' + name)
    subprocess.run(['tar', '-xJf', str(archive), '-C', '/tmp'], check=True)
PYTHON
export PKG_CONFIG_PATH=/opt/gtk4/lib/pkgconfig
export LD_LIBRARY_PATH=/opt/gtk4/lib
meson setup /tmp/pango-build /tmp/pango-1.48.10 --prefix=/opt/gtk4 --libdir=lib \
    --buildtype=release --wrap-mode=nofallback -Dintrospection=disabled
ninja -C /tmp/pango-build -j 4 install
meson setup /tmp/gtk4-build /tmp/gtk-4.0.3 --prefix=/opt/gtk4 --libdir=lib \
    --buildtype=release --wrap-mode=nofallback -Dx11-backend=true -Dwayland-backend=false \
    -Dvulkan=disabled -Dmedia-ffmpeg=disabled -Dmedia-gstreamer=disabled \
    -Dprint-cups=disabled -Dprint-cloudprint=disabled -Dintrospection=disabled \
    -Ddemos=false -Dbuild-examples=false -Dbuild-tests=false -Dsassc=disabled
ninja -C /tmp/gtk4-build -j 4 install
rm -rf /tmp/gtk4-build /tmp/gtk-4.0.3 /tmp/pango-build /tmp/pango-1.48.10
