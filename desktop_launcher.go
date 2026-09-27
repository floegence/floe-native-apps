package nativeapps

import (
	"embed"
	"os"
	"path/filepath"
	"strings"
)

//go:embed desktop_bootstrap.py desktop_package.py desktop_session.py desktop_services.py desktop_graphics.py desktop_portals.py desktop_helper.py desktop_attachment.py desktop_control.py desktop_capture.py desktop_native.py desktop_cursor.py desktop_clipboard.py desktop_context.py desktop_ibus.py desktop_ibus_service.py desktop_x11.py desktop_xim.py desktop_documents.py desktop_document_service.py application.py application_package.py application_processes.py application_peer.py application_scope.py launch_plan.py input_order.py input_marker.py input_xim.py
var desktopSources embed.FS

// WriteDesktopLauncher installs one immutable first-party Linux helper source
// snapshot in a new private directory. Invoke the returned executable with an
// owner-only version-1 launch configuration. Component must be the caller-verified
// native package for architecture; its private Python/GIO runs the helper. The helper
// owns native preparation and the existing application supervisor; a viewer or
// Runtime must not tie its lifetime to their attachment context.
//
// This installs source only. It does not authorize launch, resolve components,
// qualify a platform, or activate the unpublished combined display distribution.
// The trusted host must supply verified native resources matching its launch plan.
// Never rewrite a running instance's snapshot when updating installed support.
func WriteDesktopLauncher(directory, component, architecture string) (string, error) {
	if !filepath.IsAbs(directory) || !filepath.IsAbs(component) || strings.ContainsAny(component, ":\r\n\x00") {
		return "", ErrInvalid
	}
	loaderName := map[string]string{"amd64": "ld-musl-x86_64.so.1", "arm64": "ld-musl-aarch64.so.1"}[architecture]
	if loaderName == "" {
		return "", ErrUnsupported
	}
	root, err := filepath.EvalSymlinks(component)
	if err != nil || strings.ContainsAny(root, ":\r\n\x00") {
		return "", ErrInvalid
	}
	for _, name := range []string{"lib/" + loaderName, "usr/bin/python3", "usr/libexec/gio-launch-desktop"} {
		path, err := filepath.EvalSymlinks(filepath.Join(root, name))
		if err != nil {
			return "", err
		}
		relative, err := filepath.Rel(root, path)
		if err != nil || !filepath.IsLocal(relative) {
			return "", ErrInvalid
		}
		info, err := os.Stat(path)
		if err != nil || !info.Mode().IsRegular() || info.Mode()&0111 == 0 {
			return "", ErrInvalid
		}
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
	quote := func(value string) string { return "'" + strings.ReplaceAll(value, "'", "'\"'\"'") + "'" }
	wrapper := "#!/bin/sh\nexport PYTHONHOME=" + quote(filepath.Join(root, "usr")) + " PYTHONNOUSERSITE=1\n" +
		"export GI_TYPELIB_PATH=" + quote(filepath.Join(root, "usr/lib/girepository-1.0")) + "\n" +
		"export GIO_MODULE_DIR=" + quote(filepath.Join(root, "usr/lib/gio/modules")) + "\n" +
		"unset PYTHONPATH GTK_PATH GIO_EXTRA_MODULES LD_PRELOAD LD_LIBRARY_PATH\n" +
		"exec " + quote(filepath.Join(root, "lib", loaderName)) + " --library-path " +
		quote(filepath.Join(root, "lib")+":"+filepath.Join(root, "usr/lib")) + " " +
		quote(filepath.Join(root, "usr/bin/python3")) + " " +
		quote(filepath.Join(directory, "desktop_bootstrap.py")) + " \"$@\"\n"
	entry := filepath.Join(directory, "floe-desktop")
	if err := os.WriteFile(entry, []byte(wrapper), 0700); err != nil {
		return "", err
	}
	complete = true
	return entry, nil
}
