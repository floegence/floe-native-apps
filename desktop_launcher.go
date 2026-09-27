package nativeapps

import (
	"embed"
	"os"
	"path/filepath"
)

//go:embed desktop_bootstrap.py desktop_session.py desktop_services.py desktop_graphics.py desktop_portals.py desktop_helper.py desktop_attachment.py desktop_control.py desktop_capture.py desktop_native.py desktop_cursor.py desktop_clipboard.py desktop_context.py desktop_ibus.py desktop_ibus_service.py desktop_x11.py desktop_xim.py desktop_documents.py desktop_document_service.py application.py application_processes.py application_peer.py application_scope.py launch_plan.py input_order.py input_marker.py input_xim.py
var desktopSources embed.FS

// WriteDesktopLauncher installs one immutable first-party Linux helper source
// snapshot in a new private directory. Invoke the returned script with a verified
// Python/GIO runtime and an owner-only version-1 launch configuration. The helper
// owns native preparation and the existing application supervisor; a viewer or
// Runtime must not tie its lifetime to their attachment context.
//
// This installs source only. It does not authorize launch, resolve components,
// qualify a platform, or activate the unpublished combined display distribution.
// The trusted host must supply verified native resources matching its launch plan.
// Never rewrite a running instance's snapshot when updating installed support.
func WriteDesktopLauncher(directory string) (string, error) {
	if !filepath.IsAbs(directory) {
		return "", ErrInvalid
	}
	if err := os.Mkdir(directory, 0700); err != nil {
		return "", err
	}
	created, err := os.Lstat(directory)
	if err != nil {
		return "", err
	}
	complete := false
	defer func() {
		if !complete {
			if current, err := os.Lstat(directory); err == nil && os.SameFile(created, current) {
				_ = os.RemoveAll(directory)
			}
		}
	}()
	entries, err := desktopSources.ReadDir(".")
	if err != nil {
		return "", err
	}
	for _, entry := range entries {
		data, err := desktopSources.ReadFile(entry.Name())
		if err != nil {
			return "", err
		}
		if err := os.WriteFile(filepath.Join(directory, entry.Name()), data, 0600); err != nil {
			return "", err
		}
	}
	complete = true
	return filepath.Join(directory, "desktop_bootstrap.py"), nil
}
