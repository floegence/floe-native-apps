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
node --test desktop_frames.test.mjs host_desktop_player.test.mjs
go vet ./...
go test -race -count=1 ./...
go tool actionlint -shellcheck= -pyflakes=
python3 -m unittest host_desktop_contract_test host_desktop_wire_test host_desktop_identity_test host_desktop_portal_test host_desktop_pipewire_test host_desktop_helper_test host_desktop_media_test host_desktop_drm_test host_desktop_drm_native_test host_desktop_login_text_test host_desktop_pixels_test host_desktop_nvenc_test host_desktop_xcapture_test qualification.host_desktop_bridge_test
python3 -m unittest application_status_test application_processes_test application_peer_test application_scope_test application_package_test launch_plan_test desktop_control_test desktop_capture_test desktop_cursor_test desktop_attachment_test desktop_native_test desktop_context_test desktop_x11_test desktop_xim_test desktop_ibus_test desktop_documents_test input_marker_test input_context_test input_dispatch_test input_logging_test input_xim_test input_xpra_test display_test
python3 -m unittest desktop_services_test desktop_graphics_test desktop_portals_test desktop_helper_test desktop_session_test desktop_wayland_test desktop_clipboard_test desktop_bootstrap_test desktop_package_test desktop_document_service_test qualification.desktop_compatibility.context_probe_test qualification.desktop_compatibility.firefox_session_probe_test qualification.input_client_test
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
