package nativeapps

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func pointerFixture(t *testing.T, version string) string {
	t.Helper()
	source := t.TempDir()
	for name, fixture := range map[string]string{"index.html": "index-" + version + ".html", "js/Client.js": "Client-" + version + ".js", "js/Window.js": "Window-" + version + ".js", "js/Protocol.js": "Protocol.js"} {
		path := filepath.Join(source, name)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, inputFixture(t, fixture), 0600); err != nil {
			t.Fatal(err)
		}
	}
	output := filepath.Join(t.TempDir(), "prepared")
	if err := PrepareInputClient(source, output); err != nil {
		t.Fatal(err)
	}
	return output
}

func TestPreparedPointerHasOneOwner(t *testing.T) {
	for _, version := range []string{"v20", "v21"} {
		t.Run(version, func(t *testing.T) {
			root := pointerFixture(t, version)
			client, _ := os.ReadFile(filepath.Join(root, "js/Client.js"))
			if !strings.Contains(string(client), "this.floePointer") {
				t.Fatal("prepared client has no external pointer owner")
			}
			window, _ := os.ReadFile(filepath.Join(root, "js/Window.js"))
			for _, source := range []string{string(client), string(window)} {
				for _, retired := range []string{"on_mousedown(", "on_mousescroll(", "register_canvas_pointer_events", "pointer_down", "wheel_delta_x", "20 * window.devicePixelRatio", "jQuery(this.div).mousedown("} {
					if strings.Contains(source, retired) {
						t.Fatalf("retired pointer owner: %s", retired)
					}
				}
			}
			cmd := exec.Command("node", "--test", "pointer_test.cjs")
			cmd.Env = append(os.Environ(), "FLOE_POINTER_FIXTURE="+root)
			if out, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("pointer contract: %v\n%s", err, out)
			}

		})
	}
}

func TestPointerRejectsUnknownSource(t *testing.T) {
	if _, _, _, err := preparePointerHTML([]byte("<html>"), []byte("class XpraClient {}"), []byte("class XpraWindow {}")); err == nil {
		t.Fatal("unreviewed pointer implementation accepted")
	}
}

// Explicit browser qualification consumes the published shared controller.
func TestBrowserPointer(t *testing.T) {
	if os.Getenv("FLOE_TEST_POINTER_BROWSERS") == "" {
		t.Skip("explicit browser pointer qualification")
	}
	for _, version := range []string{"v20", "v21"} {
		t.Run(version, func(t *testing.T) {
			root := pointerFixture(t, version)
			info, _ := json.Marshal(map[string]string{"version": version, "directory": root})
			cmd := exec.Command("node", "qualification/pointer.mjs", string(info))
			if out, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("browser pointer: %v\n%s", err, out)
			} else {
				t.Log(string(out))
			}
		})
	}
}

func TestBrowserInputFocus(t *testing.T) {
	if os.Getenv("FLOE_TEST_POINTER_BROWSERS") == "" {
		t.Skip("explicit browser input focus qualification")
	}
	for _, version := range []string{"v20", "v21"} {
		t.Run(version, func(t *testing.T) {
			info, _ := json.Marshal(map[string]string{"version": version, "directory": pointerFixture(t, version)})
			cmd := exec.Command("node", "qualification/input_focus.mjs", string(info))
			if out, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("browser input focus: %v\n%s", err, out)
			} else {
				t.Log(string(out))
			}
		})
	}
}
