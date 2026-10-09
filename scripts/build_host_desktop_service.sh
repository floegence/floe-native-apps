#!/bin/sh
# Build the independent service command; it must never embed its own package.
set -eu
cd "$(dirname "$0")/.."
architecture=$1
output=$2
case "$architecture" in amd64|arm64) ;; *) exit 64 ;; esac
test ! -e "$output"
mkdir -p "$output"
GOWORK=off CGO_ENABLED=0 GOOS=linux GOARCH="$architecture" go build -trimpath -buildvcs=false -ldflags='-s -w -buildid=' -o "$output/floe-host-desktop-service" ./cmd/floe-host-desktop-service
python3 - "$architecture" "$output" <<'PY'
import hashlib, json, subprocess, sys
from pathlib import Path
arch, output = sys.argv[1], Path(sys.argv[2])
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
sources = [p for folder in ['hostdesktop', 'cmd/floe-host-desktop-service', 'internal/servicearchive'] for p in sorted(Path(folder).glob('*.go')) if not p.name.endswith('_test.go')]
sources += [Path('go.mod'), Path('go.sum'), Path('scripts/build_host_desktop_service.sh')]
binary = output / 'floe-host-desktop-service'
record = {'version': 1, 'architecture': arch, 'toolchain': subprocess.check_output(['go', 'version'], text=True).strip(),
          'sources': {str(p): digest(p) for p in sources},
          'files': {binary.name: {'sha256': digest(binary), 'size_bytes': binary.stat().st_size, 'executable': True}},
          'build_info': subprocess.check_output(['go', 'version', '-m', str(binary)], text=True),
          'status': 'Static service; administrator authorization is owned by the consuming SSH adapter'}
(output / 'manifest.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
PY
