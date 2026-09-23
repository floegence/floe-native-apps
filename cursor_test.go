package nativeapps

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestPreparedCursor(t *testing.T) {
	for _, version := range []string{"v20", "v21"} {
		t.Run(version, func(t *testing.T) {
			dir := t.TempDir()
			index, client, err := prepareInputHTML(inputFixture(t, "index-"+version+".html"), inputFixture(t, "Client-"+version+".js"))
			if err != nil {
				t.Fatal(err)
			}
			_, client, window, err := prepareCursorHTML(index, client, inputFixture(t, "Window-"+version+".js"))
			if err != nil {
				t.Fatal(err)
			}
			for name, data := range map[string][]byte{"Client": client, "Window": window} {
				if err := os.WriteFile(filepath.Join(dir, name+".js"), data, 0600); err != nil {
					t.Fatal(err)
				}
			}
			cmd := exec.Command("node", "--test", "cursor_test.cjs")
			cmd.Env = append(os.Environ(), "FLOE_CURSOR_FIXTURE="+dir)
			if output, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("cursor contract: %v\n%s", err, output)
			}
		})
	}
}

func TestCursorRejectsUnknownWindowSource(t *testing.T) {
	if _, _, _, err := prepareCursorHTML(inputFixture(t, "index-v20.html"), inputFixture(t, "Client-v20.js"), []byte("class XpraWindow {}")); err == nil {
		t.Fatal("unreviewed cursor implementation accepted")
	}
}

// Browser installation is explicit release qualification, never source CI.
func TestBrowserCursor(t *testing.T) {
	if os.Getenv("FLOE_TEST_CURSOR_BROWSERS") == "" {
		t.Skip("explicit browser cursor qualification")
	}
	for _, version := range []string{"v20", "v21"} {
		t.Run(version, func(t *testing.T) {
			dir := t.TempDir()
			index, client, err := prepareInputHTML(inputFixture(t, "index-"+version+".html"), inputFixture(t, "Client-"+version+".js"))
			if err != nil {
				t.Fatal(err)
			}
			_, client, window, err := prepareCursorHTML(index, client, inputFixture(t, "Window-"+version+".js"))
			if err != nil {
				t.Fatal(err)
			}
			adapter, _ := inputClientSource.ReadFile("input_client.js")
			for name, data := range map[string][]byte{"Client.js": client, "Window.js": window, "FloeCursor.js": cursorSource, "FloeInput.js": adapter} {
				if err := os.WriteFile(filepath.Join(dir, name), data, 0600); err != nil {
					t.Fatal(err)
				}
				if evidence := os.Getenv("FLOE_TEST_CURSOR_EVIDENCE"); evidence != "" {
					prepared := filepath.Join(evidence, "prepared-"+version)
					if err := os.MkdirAll(prepared, 0700); err != nil {
						t.Fatal(err)
					}
					if err := os.WriteFile(filepath.Join(prepared, name), data, 0600); err != nil {
						t.Fatal(err)
					}
				}
			}
			info, _ := json.Marshal(map[string]string{"version": version, "directory": dir})
			cmd := exec.Command("node", "qualification/cursor.mjs", string(info))
			if output, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("browser cursor: %v\n%s", err, output)
			} else {
				t.Log(string(output))
			}
		})
	}
}
