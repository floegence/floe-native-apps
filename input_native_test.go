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
	testNativeClientInput(t, 1)
}

func TestNativeDisplayInput(t *testing.T) {
	testNativeClientInput(t, 2)
}

func testNativeClientInput(t *testing.T, density int) {
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
	python, xvfb, dbus := tools.Python, tools.Xvfb, filepath.Join(root, "floe", "bin", "dbus-daemon")
	serverEnvironment := environment
	if os.Getenv("FLOE_TEST_INPUT_SYSTEM") == "1" {
		xpra, err := exec.LookPath("xpra")
		if err != nil {
			t.Fatal(err)
		}
		serverEnvironment = os.Environ()
		python, err = SystemClientInputPython(ctx, xpra, serverEnvironment)
		if err != nil {
			t.Fatal(err)
		}
		xvfb, err = exec.LookPath("Xvfb")
		if err != nil {
			t.Fatal(err)
		}
		dbus, err = exec.LookPath("dbus-daemon")
		if err != nil {
			t.Fatal(err)
		}
	}
	capability, err := ProbeClientInput(ctx, python, serverEnvironment)
	if err != nil {
		t.Fatal(err)
	}
	state := t.TempDir()
	input, err := PrepareClientInput(filepath.Join(state, "input"), runtime.GOARCH)
	if err != nil {
		t.Fatal(err)
	}
	applicationLauncher, err := WriteApplicationLauncher(state)
	if err != nil {
		t.Fatal(err)
	}
	if err := PrepareInputClient(tools.HTML, filepath.Join(state, "www")); err != nil {
		t.Fatal(err)
	}
	assets, err := OpenClientAssets(filepath.Join(state, "www"))
	if err != nil {
		t.Fatal(err)
	}
	index, err := os.ReadFile(filepath.Join(state, "www", "index.html"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := assets.RewriteHTML(index, "/client/"+assets.Digest()+"/"); err != nil {
		t.Fatal(err)
	}
	t.Logf("Prepared client resource version: %s", assets.Digest())
	config := filepath.Join(state, "config.json")
	data, err := json.Marshal(map[string]any{"root": root, "state": state, "python": python,
		"xvfb": xvfb, "dbus": dbus, "client_python": tools.Python, "client_environment": environment,
		"launcher": input.Launcher, "application_launcher": applicationLauncher,
		"args": input.XpraArgs(os.Environ()), "environment": serverEnvironment,
		"capability": capability, "density": density})
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
