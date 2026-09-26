#!/bin/sh
# Fast, source-only checks. Native graphical qualification is separate.
set -eu
cd "$(dirname "$0")/.."
export GOWORK=off
expected=$(awk '$1 == "go" {print $2}' go.mod)
actual=$(go env GOVERSION)
if [ "$actual" != "go$expected" ]; then
  echo "Use Go $expected (go.mod); found $actual" >&2
  exit 1
fi
unformatted=$(gofmt -l .)
if [ -n "$unformatted" ]; then
  echo "$unformatted" >&2
  exit 1
fi
go mod tidy -diff
go mod verify
go vet ./...
go test -race -count=1 ./...
go tool actionlint -shellcheck= -pyflakes=
python3 -m unittest application_status_test application_processes_test application_peer_test application_scope_test launch_plan_test desktop_control_test desktop_capture_test desktop_attachment_test desktop_native_test desktop_context_test desktop_x11_test desktop_xim_test desktop_ibus_test desktop_documents_test input_marker_test input_context_test input_dispatch_test input_logging_test input_xim_test input_xpra_test display_test
python3 - <<'PY'
import ast
from pathlib import Path
for path in [*Path('.').glob('*.py'), *Path('scripts').glob('*.py'), *Path('selfcheck').glob('*.py'), *Path('qualification').glob('*.py'), *Path('qualification/desktop_compatibility').glob('*.py')]:
    ast.parse(path.read_text(), filename=str(path))
PY
for script in scripts/*.sh qualification/desktop_compatibility/*.sh .githooks/pre-push; do sh -n "$script"; done
for target in linux/amd64 linux/arm64 darwin/amd64 darwin/arm64 windows/amd64 windows/arm64; do
  GOOS=${target%/*} GOARCH=${target#*/} CGO_ENABLED=0 go build ./...
done
