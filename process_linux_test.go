//go:build linux

package nativeapps

import (
	"os"
	"os/exec"
	"strings"
	"testing"
)

func TestProcessGenerationRejectsStaleAndExitedIdentity(t *testing.T) {
	p, err := ObserveProcess(os.Getpid())
	if err != nil || !p.Alive() {
		t.Fatalf("current process: %v", err)
	}
	p.Started += "1"
	if p.Alive() {
		t.Fatal("stale process generation accepted")
	}
	cmd := exec.Command("sleep", "30")
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer cmd.Process.Kill()
	p, err = ObserveProcess(cmd.Process.Pid)
	if err != nil || !p.Alive() {
		t.Fatalf("child process: %v", err)
	}
	_ = cmd.Process.Kill()
	_ = cmd.Wait()
	if p.Alive() {
		t.Fatal("exited process remains live")
	}
}

func TestProcessStatNamesCannotShiftStartIdentity(t *testing.T) {
	stat := "42 (a name ) with spaces) S " + strings.Repeat("0 ", 18) + "123"
	if start, err := processStart(stat); err != nil || start != "123" {
		t.Fatal(start, err)
	}
	for _, invalid := range []string{"", "42 (name) S", strings.Replace(stat, ") S ", ") Z ", 1)} {
		if _, err := processStart(invalid); err == nil {
			t.Fatal("invalid/dead process accepted")
		}
	}
}
