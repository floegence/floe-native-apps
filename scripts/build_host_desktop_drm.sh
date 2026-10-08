#!/bin/sh
# Run inside the pinned disposable glibc builder, with the reviewed libdrmtap
# checkout mounted read-only. The resulting worker uses the host's GPU driver;
# no host libraries, permissions or system packages are changed by this build.
set -eu
: "${FLOE_DRM_BUILDER_IMAGE:?Set the immutable builder image digest}"
test -f /builder-packages.sha256
source_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
third_party_root=$1
output=$2
test ! -e "$output"
expected_commit=95d4d74549631aa5c39461300acfd2e106583cc9
test "$(git -c safe.directory="$third_party_root" -C "$third_party_root" rev-parse HEAD)" = "$expected_commit"
test -z "$(git -c safe.directory="$third_party_root" -C "$third_party_root" status --porcelain)"
mkdir -p "$output/objects"
cd "$output/objects"
for name in drmtap drm_enumerate drm_grab gpu_generic pixel_convert gpu_intel gpu_amd gpu_nvidia gpu_egl cursor privilege_helper_stub; do
  cc -std=c11 -O2 -Wall -Wextra -Werror -fstack-protector-strong -D_FORTIFY_SOURCE=2 \
    -DDRMTAP_NO_HELPER=1 -DHAVE_EGL=1 -I "$third_party_root/include" \
    -I "$third_party_root/src" -I /usr/include/libdrm -c "$third_party_root/src/$name.c" -o "$name.o"
done
ar rcs "$output/libdrmtap.a" ./*.o
cc -std=c11 -O2 -Wall -Wextra -Werror -fstack-protector-strong -D_FORTIFY_SOURCE=2 \
  -Wl,-z,relro,-z,now -Wl,--build-id=none -I "$third_party_root/include" \
  -o "$output/desktop-drm" "$source_root/native/host-desktop/drm/worker.c" \
  "$output/libdrmtap.a" -ldrm -ldl -lm -pthread
strip --strip-unneeded "$output/desktop-drm"
cp "$third_party_root/LICENSE" "$output/libdrmtap.LICENSE"
python3 - "$source_root" "$third_party_root" "$output" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
source, dependency, output = map(Path, sys.argv[1:])
architecture = {'x86_64': 'amd64', 'aarch64': 'arm64'}[platform.machine()]
binary = output / 'desktop-drm'
elf = subprocess.check_output(['readelf', '-hldV', str(binary)], text=True)
if '(RPATH)' in elf or '(RUNPATH)' in elf:
    raise ValueError('Unexpected loader override')
versions = {tuple(map(int, value.split('.'))) for value in re.findall(r'GLIBC_([0-9.]+)', elf)}
if max(versions) > (2, 31):
    raise ValueError('glibc baseline exceeded')
needed = set(re.findall(r'Shared library: \[(.*?)\]', elf))
allowed = {'libdrm.so.2', 'libc.so.6', 'libdl.so.2', 'libm.so.6', 'libpthread.so.0'}
if architecture == 'arm64':
    allowed.add('ld-linux-aarch64.so.1')
if needed - allowed:
    raise ValueError('Unexpected dynamic dependency')
symbols = subprocess.check_output(['nm', '-D', str(binary)], text=True)
if re.search(r'\b(fork|execve|execl|execvp|posix_spawn)\b', symbols):
    raise ValueError('Unexpected privileged helper launch path')
digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
paths = ['native/host-desktop/drm/worker.c', 'native/host-desktop/drm/source.json',
         'native/host-desktop/drm/Dockerfile', 'scripts/build_host_desktop_drm.sh']
record = {'version': 1, 'architecture': architecture,
          'builder_image': os.environ['FLOE_DRM_BUILDER_IMAGE'],
          'compiler': subprocess.check_output(['cc', '--version'], text=True).splitlines()[0],
          'signed_builder_packages': Path('/builder-packages.sha256').read_text().splitlines(),
          'sources': {path: digest(source / path) for path in paths},
          'dependency': json.loads((source / paths[1]).read_text()),
          'dependency_sources': {str(path.relative_to(dependency)): digest(path)
                                 for folder in ['include', 'src']
                                 for path in sorted((dependency / folder).glob('*')) if path.is_file()},
          'files': {name: {'sha256': digest(output / name), 'size_bytes': (output / name).stat().st_size,
                           'executable': name == 'desktop-drm'}
                    for name in ['desktop-drm', 'libdrmtap.LICENSE']}, 'elf': elf,
          'status': 'Split DRM export and unprivileged GPU conversion; physical scanout qualification is required'}
(output / 'manifest.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
PY
rm -rf "$output/objects" "$output/libdrmtap.a"
