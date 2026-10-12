#!/bin/sh
# Run only inside the pinned disposable glibc builder, on its native architecture.
set -eu
test -f /builder-packages.sha256
: "${FLOE_MPP_BUILDER_IMAGE:?Set the immutable builder image digest}"
source_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
output=$1
test ! -e "$output"
mkdir -p "$output"
cd "$source_root"
cc -std=c11 -O2 -Wall -Wextra -Werror -fstack-protector-strong -D_FORTIFY_SOURCE=2 \
  -Wl,-z,relro,-z,now -Wl,--build-id=none -I native/host-desktop/vendor/mpp native/host-desktop/mpp.c -ldl -o "$output/desktop-mpp"
strip --strip-unneeded "$output/desktop-mpp"
python3 - "$source_root" "$output" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
source, output = map(Path, sys.argv[1:])
architecture = {'x86_64':'amd64', 'aarch64':'arm64'}[platform.machine()]
binary = output / 'desktop-mpp'
elf = subprocess.check_output(['readelf', '-hldV', str(binary)], text=True)
if '(RPATH)' in elf or '(RUNPATH)' in elf:
    raise ValueError('Unexpected loader override')
expected = 'AArch64' if architecture == 'arm64' else 'Advanced Micro Devices X86-64'
if expected not in elf:
    raise ValueError('Incorrect native architecture')
versions = {tuple(map(int, value.split('.'))) for value in re.findall(r'GLIBC_([0-9.]+)', elf)}
if max(versions) > (2,31):
    raise ValueError('glibc baseline exceeded')
needed = re.findall(r'Shared library: \[(.*?)\]', elf)
allowed = {'libc.so.6','libdl.so.2'} | ({'ld-linux-aarch64.so.1'} if architecture == 'arm64' else set())
if set(needed) - allowed:
    raise ValueError('Unexpected dynamic dependency')
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
paths = ['native/host-desktop/mpp.c', 'native/host-desktop/native_pixels.h', 'native/host-desktop/drm/Dockerfile',
    'scripts/build_host_desktop_mpp.sh']
paths += [str(p.relative_to(source)) for p in sorted((source / 'native/host-desktop/vendor/mpp').iterdir()) if p.is_file()]
record = {'version':1, 'architecture':architecture, 'builder_image':os.environ['FLOE_MPP_BUILDER_IMAGE'],
    'compiler':subprocess.check_output(['cc','--version'],text=True).splitlines()[0],
    'signed_builder_packages':Path('/builder-packages.sha256').read_text().splitlines(),
    'sources':{name:digest(source/name) for name in paths},
    'files':{'desktop-mpp':{'sha256':digest(binary),'size_bytes':binary.stat().st_size,'executable':True}},
    'elf':elf, 'headers_source':'https://github.com/rockchip-linux/mpp/tree/c08762ebfadeb4e986d2fed993bc7a54862d3ebe',
    'status':'Native glibc 2.31 build; driver and actual-size encode probes determine availability'}
(output/'manifest.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
subprocess.run([str(binary),'--check'],check=True)
PY
