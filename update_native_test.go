package nativeapps

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

// The caller provides a disposable copy of a published r1 installation and its
// archive cache. Never point this opt-in test at a user's production state.
func TestNativeLegacyComponentUpdate(t *testing.T) {
	root := os.Getenv("FLOE_TEST_LEGACY_STATE")
	if root == "" {
		t.Skip("requires a disposable published r1 installation")
	}
	if runtime.GOOS != "linux" {
		t.Fatal("native Linux qualification required")
	}
	pkg, err := NativePackage()
	if err != nil {
		t.Fatal(err)
	}
	m, err := New(root, pkg, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	s := m.Snapshot("qualification")
	if s.Installed == nil || !s.Installed.Ready || !s.UpdateAvailable {
		t.Fatal(s)
	}
	previous := s.Installed.Digest
	oldRoot, err := m.DirectoryFor(previous)
	if err != nil {
		t.Fatal(err)
	}
	oldTools, err := ResolveTools(oldRoot)
	if err != nil {
		t.Fatal(err)
	}
	process := exec.Command(oldTools.Python, "-c", "import time; time.sleep(180)")
	process.Env = oldTools.Environment(os.Environ())
	if err := process.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = process.Process.Kill(); _ = process.Wait() }()
	identity, err := ObserveProcess(process.Process.Pid)
	if err != nil {
		t.Fatal(err)
	}
	// Wait for exec to enter the private ELF loader before inspecting it.
	deadline := time.Now().Add(3 * time.Second)
	for {
		selected, err := m.InstallationForProcess(identity)
		if err == nil && selected != nil && selected.Digest == previous {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("could not identify legacy process installation", selected, err)
		}
		time.Sleep(10 * time.Millisecond)
	}
	marker := filepath.Join(root, "packages", previous, ".native-apps")
	before, err := os.ReadFile(marker)
	if err != nil {
		t.Fatal(err)
	}
	plan, err := m.Plan(context.Background())
	if err != nil || plan.MissingBytes != 0 {
		t.Fatal(plan, err)
	}
	m.client.Transport = rejectNetwork{t}
	ch, stop := m.Watch()
	defer stop()
	if _, err := m.Start("qualification", "explicit-update", "cache", 0); err != nil {
		t.Fatal(err)
	}
	timeout := time.NewTimer(2 * time.Minute)
	defer timeout.Stop()
	for {
		select {
		case <-timeout.C:
			t.Fatal("component update timed out")
		case <-ch:
			s = m.Snapshot("qualification")
			if s.Active() {
				if s.Installed == nil || !s.Installed.Ready || s.Installed.Digest != previous {
					t.Fatal("current installation changed before commit", s)
				}
				continue
			}
			if s.State != "ready" || s.UpdateAvailable || s.Installed == nil || s.Installed.Digest != pkg.Digest() || s.ReceivedBytes != 0 {
				t.Fatal(s)
			}
			after, err := os.ReadFile(marker)
			if err != nil || string(after) != string(before) {
				t.Fatal("rewrote legacy installation", err)
			}
			if _, err := m.DirectoryFor(previous); err != nil {
				t.Fatal("removed the previous package", err)
			}
			selected, err := m.InstallationForProcess(identity)
			if err != nil || selected == nil || selected.Digest != previous || !identity.Alive() {
				t.Fatal("running backend lost its original installation", selected, err)
			}
			t.Logf("retained %s and activated %s without network or relay", previous, pkg.Digest())
			return
		}
	}
}
