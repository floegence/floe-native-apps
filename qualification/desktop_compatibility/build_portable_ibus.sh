#!/bin/sh
# Build only in the recorded native Alpine builder; never install on a user host.
set -eu
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
archive=$1
source_patch=$2
output=$3
: "${FLOE_DESKTOP_BUILDER_IMAGE:?Set the immutable native builder image identity}"
test "$(cat /etc/alpine-release)" = 3.23.6
test -f /builder-apks.sha256
export SOURCE_DATE_EPOCH=0
export CFLAGS='-O2 -fstack-protector-strong'
export LDFLAGS='-Wl,-z,relro,-z,now'
sh "$source_dir/build_ibus.sh" "$archive" "$source_patch" "$output"
artifacts=$output/artifacts
mkdir "$artifacts"
for entry in bus/.libs/ibus-daemon portal/.libs/ibus-portal src/.libs/libibus-1.0.so.5.0.533; do
    cp "$output/ibus-1.5.33/$entry" "$artifacts/"
    strip --strip-unneeded "$artifacts/$(basename "$entry")"
done
python3 - "$output" "$archive" "$source_patch" "$source_dir" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

output, archive, patch, source = map(Path, sys.argv[1:])
artifacts = output / 'artifacts'
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
record = {
    'architecture': platform.machine(),
    'builder_image': os.environ['FLOE_DESKTOP_BUILDER_IMAGE'],
    'libc': 'musl',
    'original_source_sha256': digest(archive),
    'patch_sha256': digest(patch),
    'build_source_sha256': {name: digest(source / name) for name in
        ('Dockerfile.ibus-portable', 'build_portable_ibus.sh', 'build_ibus.sh')},
    'signed_builder_archives': Path('/builder-apks.sha256').read_text().splitlines(),
    'packages': subprocess.check_output(['apk', 'info', '-vv'], text=True).splitlines(),
    'compiler': subprocess.check_output(['cc', '--version'], text=True).splitlines()[0],
    'artifacts': {p.name: {'sha256': digest(p),
        'elf': subprocess.check_output(['readelf', '-hld', str(p)], text=True)}
        for p in sorted(artifacts.iterdir())},
    'license': 'LGPL-2.1-or-later',
    'original_license_sha256': digest(output / 'ibus-1.5.33/COPYING'),
    'status': 'unactivated native distribution candidate; application qualification required',
}
(output / 'build.json').write_text(json.dumps(record, indent=2) + '\n')
PY
