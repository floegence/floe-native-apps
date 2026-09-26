package nativeapps

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
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

func TestNativeViewerLayout(t *testing.T) {
	t.Setenv("FLOE_TEST_LAYOUT_NATIVE", "1")
	t.Setenv("FLOE_TEST_WINDOW_COLOR", "13579b")
	if os.Getenv("FLOE_TEST_INPUT_FIXTURES") == "" {
		t.Setenv("FLOE_TEST_INPUT_FIXTURES", "gtk,gtk4,qt5,qt6")
	}
	t.Setenv("FLOE_TEST_WINDOW_ACTIONS", "1")
	t.Run("v20", func(t *testing.T) { testNativeClientInput(t, 1) })
	if source := os.Getenv("FLOE_TEST_LAYOUT_HTML_V21"); source != "" {
		t.Run("v21", func(t *testing.T) {
			t.Setenv("FLOE_TEST_VIEWER_HTML", source)
			if evidence := os.Getenv("FLOE_TEST_INPUT_EVIDENCE"); evidence != "" {
				t.Setenv("FLOE_TEST_INPUT_EVIDENCE", filepath.Join(evidence, "html-v21"))
			}
			testNativeClientInput(t, 1)
		})
	}
}

func TestNativeDisplayInput(t *testing.T) {
	testNativeClientInput(t, 2)
}

func TestNativeViewerUpgrade(t *testing.T) {
	legacy := os.Getenv("FLOE_TEST_LEGACY_VIEWER_FIXTURE")
	if legacy == "" {
		t.Skip("explicit published legacy viewer fixture")
	}
	t.Setenv("FLOE_TEST_LEGACY_VIEWER", legacy)
	t.Setenv("FLOE_TEST_LAYOUT_NATIVE", "1")
	t.Setenv("FLOE_TEST_WINDOW_COLOR", "13579b")
	t.Setenv("FLOE_TEST_INPUT_FIXTURES", "gtk")
	testNativeClientInput(t, 1)
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
	htmlSource := tools.HTML
	if source := os.Getenv("FLOE_TEST_VIEWER_HTML"); source != "" {
		htmlSource = source
	}
	if err := PrepareInputClient(htmlSource, filepath.Join(state, "www")); err != nil {
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
	viewer, err := PrepareViewer(htmlSource)
	if err != nil {
		t.Fatal(err)
	}
	assetPath := "/assets/" + viewer.Assets().Digest() + "/"
	document, err := viewer.Document(assetPath)
	if err != nil {
		t.Fatal(err)
	}
	mux := http.NewServeMux()
	mux.Handle(assetPath, http.StripPrefix(assetPath[:len(assetPath)-1], viewer.Assets()))
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = w.Write(document)
	})
	server := httptest.NewServer(mux)
	defer server.Close()
	args := input.XpraArgs(os.Environ())
	legacyHTML := ""
	if path := os.Getenv("FLOE_TEST_LEGACY_VIEWER"); path != "" {
		var legacy struct {
			Launcher string
			Args     []string
			HTML     string
		}
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal(data, &legacy); err != nil {
			t.Fatal(err)
		}
		input.Launcher, args, legacyHTML = legacy.Launcher, legacy.Args, legacy.HTML
	}
	config := filepath.Join(state, "config.json")
	data, err := json.Marshal(map[string]any{"root": root, "state": state, "python": python,
		"xvfb": xvfb, "dbus": dbus, "client_python": tools.Python, "client_environment": environment,
		"launcher": input.Launcher, "application_launcher": applicationLauncher,
		"args": args, "environment": serverEnvironment,
		"viewer_url": server.URL, "legacy_html": legacyHTML,
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
