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
python3 - <<'PY'
import ast
from pathlib import Path
for path in [*Path('scripts').glob('*.py'), *Path('selfcheck').glob('*.py')]:
    ast.parse(path.read_text(), filename=str(path))
PY
for script in scripts/*.sh .githooks/pre-push; do sh -n "$script"; done
for target in linux/amd64 linux/arm64 darwin/amd64 darwin/arm64 windows/amd64 windows/arm64; do
  GOOS=${target%/*} GOARCH=${target#*/} CGO_ENABLED=0 go build ./...
done
