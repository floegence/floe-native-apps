package nativeapps

import (
	_ "embed"
	"os"
	"path/filepath"
)

//go:embed application.py
var applicationLauncher []byte

// XpraApplicationLifetimeArgs binds an Xpra server to a monitored application
// child, independently of its viewers and windows. Use a --start-child command
// that remains alive until the application and its descendants have exited.
// Consumers still own application authorization, persistence and session policy.
func XpraApplicationLifetimeArgs() []string {
	return []string{"--exit-with-client=no", "--exit-with-windows=no", "--exit-with-children=yes"}
}

// WriteApplicationLauncher installs the Linux GIO launcher in a consumer-owned
// private directory. Invoke it with the tools' Python, an absolute .desktop file
// and an absolute receipt path. The atomic receipt reports running, exited or
// failed. A running receipt confirms process launch, not window/pixel readiness.
// The launcher restores the host application environment and reaps descendants;
// closing a window or losing a viewer never terminates the application.
func WriteApplicationLauncher(directory string) (string, error) {
	if !filepath.IsAbs(directory) {
		return "", ErrInvalid
	}
	f, err := os.CreateTemp(directory, ".application-*.py")
	if err != nil {
		return "", err
	}
	defer os.Remove(f.Name())
	if _, err = f.Write(applicationLauncher); err != nil {
		_ = f.Close()
		return "", err
	}
	if err = f.Close(); err != nil {
		return "", err
	}
	path := filepath.Join(directory, "floe-application.py")
	if err = os.Rename(f.Name(), path); err != nil {
		return "", err
	}
	return path, nil
}
