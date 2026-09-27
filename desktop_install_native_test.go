package nativeapps

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

// Explicit native qualification consumes original candidate bytes through the
// production Manager and its graphical activation check. It changes no host
// package, desktop configuration or existing application's installation.
func TestNativeDesktopInstallation(t *testing.T) {
	candidate := os.Getenv("FLOE_TEST_DESKTOP_INSTALL_CANDIDATE")
	state := os.Getenv("FLOE_TEST_DESKTOP_INSTALL_STATE")
	if candidate == "" {
		t.Skip("explicit native desktop installation fixture")
	}
	if runtime.GOOS != "linux" || !filepath.IsAbs(candidate) || !filepath.IsAbs(state) {
		t.Fatal("native private fixture paths required")
	}
	if _, err := os.Lstat(state); !os.IsNotExist(err) {
		t.Fatal("new qualification state required", err)
	}
	pkg, err := DesktopForPlatform("linux", runtime.GOARCH)
	if err != nil {
		t.Fatal(err)
	}
	manager, err := New(state, pkg, func(ctx context.Context, root string) error {
		evidence := filepath.Join(state, "selfcheck")
		if err := os.Mkdir(evidence, 0700); err != nil {
			return err
		}
		err := runDesktopSelfTest(ctx, root, pkg, func(name string, data []byte) error {
			return os.WriteFile(filepath.Join(evidence, name), data, 0600)
		})
		if err != nil {
			t.Log(err)
		}
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	defer manager.Close()
	if err := os.Mkdir(filepath.Join(state, "archives"), 0700); err != nil {
		t.Fatal(err)
	}
	for _, artifact := range pkg.Artifacts {
		path := filepath.Join(candidate, "apks", artifact.Name)
		if !verifyFile(path, artifact) {
			t.Fatal("original archive differs from compiled desktop catalog", artifact.Name)
		}
		source, err := os.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		destination, err := os.OpenFile(manager.artifactPath(artifact), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if err != nil {
			_ = source.Close()
			t.Fatal(err)
		}
		_, err = io.Copy(destination, source)
		_ = source.Close()
		closed := destination.Close()
		if err != nil || closed != nil {
			t.Fatal(err)
		}
	}
	if _, err := manager.Start("qualification", "native-desktop", "cache", 0); err != nil {
		t.Fatal(err)
	}
	changes, stop := manager.Watch()
	defer stop()
	timeout := time.NewTimer(2 * time.Minute)
	defer timeout.Stop()
	for {
		select {
		case <-timeout.C:
			t.Fatal("native desktop installation timeout", manager.Snapshot("qualification"))
		case <-changes:
			status := manager.Snapshot("qualification")
			if status.Active() {
				continue
			}
			if status.State != "ready" || status.Installed == nil || !status.Installed.Ready {
				t.Fatalf("native desktop did not activate: %+v", status)
			}
			root, err := manager.Directory()
			if err != nil {
				t.Fatal(err)
			}
			backend, err := manager.DesktopBackend()
			if err != nil {
				t.Fatal(err)
			}
			data, err := json.MarshalIndent(map[string]any{"package": pkg.ID, "digest": pkg.Digest(), "root": root, "backend": backend, "activated": true}, "", "  ")
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(state, "qualification.json"), append(data, '\n'), 0600); err != nil {
				t.Fatal(err)
			}
			qualifyInstalledDesktopPlanning(t, manager)
			t.Logf("installed and qualified native %s desktop %s", runtime.GOARCH, pkg.Digest())
			return
		}
	}
}

func qualifyInstalledDesktopPlanning(t *testing.T, manager *Manager) {
	t.Helper()
	directory := t.TempDir()
	runtime := filepath.Join(directory, "runtime")
	if err := os.Mkdir(runtime, 0700); err != nil {
		t.Fatal(err)
	}
	desktop := filepath.Join(directory, "fixture.desktop")
	source := "[Desktop Entry]\nType=Application\nName=Plan rejection check\nExec=/bin/true\n"
	if err := os.WriteFile(desktop, []byte(source), 0600); err != nil {
		t.Fatal(err)
	}
	plan, err := manager.PlanDesktop(context.Background(), desktop, os.Environ())
	if err != nil {
		t.Fatal(err)
	}
	options := DesktopSessionOptions{Directory: filepath.Join(directory, "instance"), Runtime: runtime,
		Instance: "planning-check", Plan: plan, Environment: os.Environ()}
	// Real GIO revalidation, without executing an application, must precede any
	// creation of a graphical session or its private credentials.
	if err := os.WriteFile(desktop, []byte(source+"Comment=Updated package\n"), 0600); err != nil {
		t.Fatal(err)
	}
	_, err = manager.PrepareDesktopSession(context.Background(), options)
	var stale *LaunchUnavailable
	if !errors.As(err, &stale) || stale.Code != "APPLICATION_PLAN_STALE" {
		t.Fatal("stale plan accepted", err)
	}
	if _, err := os.Lstat(options.Directory); !os.IsNotExist(err) {
		t.Fatal("stale plan created session resources", err)
	}
	options.Plan, err = manager.PlanDesktop(context.Background(), desktop, os.Environ())
	if err != nil {
		t.Fatal(err)
	}
	prepared, err := manager.PrepareDesktopSession(context.Background(), options)
	if err != nil {
		t.Fatal(err)
	}
	before, err := os.ReadFile(prepared.Configuration)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := manager.PrepareDesktopSession(context.Background(), options); !os.IsExist(err) {
		t.Fatal("existing instance overwritten", err)
	}
	after, err := os.ReadFile(prepared.Configuration)
	if err != nil || string(before) != string(after) {
		t.Fatal("existing instance changed", err)
	}
	if info, err := os.Lstat(prepared.Configuration); err != nil || info.Mode().Perm() != 0600 {
		t.Fatal("private configuration permissions", err)
	}
}
