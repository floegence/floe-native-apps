package nativeapps

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

// Native qualification uses only task-owned displays, ports, profiles and state.
// Real toolkits and the browser report their actual document/terminal contents.
func TestNativeClientInput(t *testing.T) {
	root := os.Getenv("FLOE_TEST_INPUT_ROOT")
	if root == "" {
		t.Skip("explicit native application qualification")
	}
	if runtime.GOOS != "linux" {
		t.Fatal("native Linux qualification required")
	}
	tools, err := ResolveTools(root)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	environment := tools.Environment(os.Environ())
	capability, err := ProbeClientInput(ctx, tools.Python, environment)
	if err != nil {
		t.Fatal(err)
	}
	state := t.TempDir()
	input, err := PrepareClientInput(filepath.Join(state, "input"), runtime.GOARCH)
	if err != nil {
		t.Fatal(err)
	}
	if err := PrepareInputClient(tools.HTML, filepath.Join(state, "www")); err != nil {
		t.Fatal(err)
	}
	config := filepath.Join(state, "config.json")
	data, err := json.Marshal(map[string]any{"root": root, "state": state, "python": tools.Python,
		"xvfb": tools.Xvfb, "dbus": filepath.Join(root, "floe", "bin", "dbus-daemon"),
		"launcher": input.Launcher, "args": input.XpraArgs(os.Environ()), "environment": environment,
		"capability": capability})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(config, data, 0600); err != nil {
		t.Fatal(err)
	}
	command := exec.CommandContext(ctx, "python3", "qualification/input.py", config)
	command.Env = os.Environ()
	if out, err := command.CombinedOutput(); err != nil {
		t.Fatalf("native client input: %v\n%s", err, out)
	} else {
		t.Logf("%s", out)
	}
}
