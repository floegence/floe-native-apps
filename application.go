package nativeapps

import (
	_ "embed"
	"os"
	"path/filepath"
)

//go:embed application.py
var applicationLauncher []byte

//go:embed application_processes.py
var applicationProcesses []byte

//go:embed application_scope.py
var applicationScope []byte

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
// Spawn failures include phase and a stable error_code. Final receipts include
// the actual direct-launcher exit_code (negative for a signal), per-launcher
// statuses and termination_requested. These are process observations; the
// graphical backend determines whether launch or established execution ended.
// The supervisor retains a nonzero launcher result after reaping descendants.
// The launcher restores the host application environment and reaps descendants;
// closing a window or losing a viewer never terminates the application.
// A verified Snap plan additionally requires FLOE_NATIVE_HOST_BUS to identify
// the real user bus, separate from DBUS_SESSION_BUS_ADDRESS. Only the supervisor
// receives that handoff; it is removed from the final application's environment.
// The supervisor exposes only its admitted direct launcher's scope operation.
func WriteApplicationLauncher(directory string) (string, error) {
	for name, data := range map[string][]byte{"launch_plan.py": applicationPlanner,
		"application_processes.py": applicationProcesses, "application_scope.py": applicationScope} {
		if _, err := writeApplicationFile(directory, name, data, true); err != nil {
			return "", err
		}
	}
	return writeApplicationFile(directory, "floe-application.py", applicationLauncher, true)
}

func writeApplicationFile(directory, name string, data []byte, replace bool) (string, error) {
	if !filepath.IsAbs(directory) {
		return "", ErrInvalid
	}
	f, err := os.CreateTemp(directory, ".application-*.py")
	if err != nil {
		return "", err
	}
	defer os.Remove(f.Name())
	if _, err = f.Write(data); err != nil {
		_ = f.Close()
		return "", err
	}
	if err = f.Close(); err != nil {
		return "", err
	}
	path := filepath.Join(directory, name)
	if replace {
		err = os.Rename(f.Name(), path)
	} else {
		// Publish a complete new snapshot atomically without rewriting a live
		// instance if its directory was accidentally selected a second time.
		err = os.Link(f.Name(), path)
	}
	if err != nil {
		return "", err
	}
	return path, nil
}
