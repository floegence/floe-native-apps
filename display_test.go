package nativeapps

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestPreparedDisplay(t *testing.T) {
	for _, version := range []string{"v20", "v21"} {
		t.Run(version, func(t *testing.T) {
			dir := t.TempDir()
			index, client, err := prepareInputHTML(inputFixture(t, "index-"+version+".html"), inputFixture(t, "Client-"+version+".js"))
			if err != nil {
				t.Fatal(err)
			}
			index, client, window, err := prepareCursorHTML(index, client, inputFixture(t, "Window-"+version+".js"))
			if err != nil {
				t.Fatal(err)
			}
			_, client, window, err = prepareDisplayHTML(index, client, window)
			if err != nil {
				t.Fatal(err)
			}
			for name, data := range map[string][]byte{"Client": client, "Window": window} {
				if err := os.WriteFile(filepath.Join(dir, name+".js"), data, 0600); err != nil {
					t.Fatal(err)
				}
			}
			cmd := exec.Command("node", "--test", "display_test.cjs")
			cmd.Env = append(os.Environ(), "FLOE_DISPLAY_FIXTURE="+dir)
			if out, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("display contract: %v\n%s", err, out)
			}
		})
	}
}
