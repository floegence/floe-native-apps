//go:build linux

package hostdesktop

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestLoginDriverBootConfigAndRollback(t *testing.T) {
	path := filepath.Join(t.TempDir(), "redeven-desktop.conf")
	loaded := false
	d := loginDeployer{driverFile: path, driverPresent: func() bool { return loaded }, loadDriver: func(_ context.Context, unload bool) error { loaded = !unload; return nil }}
	undo, err := d.prepareDriver(context.Background(), "install")
	if err != nil || !loaded {
		t.Fatal("driver not loaded")
	}
	if data, _ := os.ReadFile(path); string(data) != loginDriverConfig {
		t.Fatal("boot setup missing")
	}
	if err := undo(context.Background()); err != nil || loaded {
		t.Fatal("driver rollback failed")
	}
	if _, err := os.Stat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("partial boot config retained")
	}
	loaded = true
	undo, err = d.prepareDriver(context.Background(), "install")
	if err != nil {
		t.Fatal(err)
	}
	if err := undo(context.Background()); err != nil || !loaded {
		t.Fatal("pre-existing driver was unloaded")
	}
	if err := os.WriteFile(path, []byte("unrelated configuration"), 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := d.prepareDriver(context.Background(), "install"); err == nil {
		t.Fatal("unrelated boot config adopted")
	}
}
func TestLoginDriverUninstallRollbackPreservesBootSetup(t *testing.T) {
	path := filepath.Join(t.TempDir(), "redeven-desktop.conf")
	if err := os.WriteFile(path, []byte(loginDriverConfig), 0644); err != nil {
		t.Fatal(err)
	}
	d := loginDeployer{driverFile: path}
	undo, err := d.prepareDriver(context.Background(), "uninstall")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("boot config not removed")
	}
	if err := undo(context.Background()); err != nil {
		t.Fatal(err)
	}
	if data, _ := os.ReadFile(path); string(data) != loginDriverConfig {
		t.Fatal("boot setup not restored")
	}
}

func TestLoginDriverCancellationBeforeDeviceReadyUndoesNewModule(t *testing.T) {
	path := filepath.Join(t.TempDir(), "redeven-desktop.conf")
	loaded := false
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	d := loginDeployer{driverFile: path, driverPresent: func() bool { return false }, driverLoaded: func() bool { return loaded },
		loadDriver: func(_ context.Context, unload bool) error {
			loaded = !unload
			if !unload {
				cancel()
			}
			return nil
		}}
	undo, err := d.prepareDriver(ctx, "install")
	if !errors.Is(err, context.Canceled) || !loaded {
		t.Fatal("device readiness ignored cancellation")
	}
	if err = undo(context.Background()); err != nil || loaded {
		t.Fatal("new module survived canceled readiness")
	}
	if _, err := os.Stat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("canceled readiness left boot configuration")
	}
}
