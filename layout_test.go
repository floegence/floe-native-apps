package nativeapps

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestPreparedLayoutAndCapabilities(t *testing.T) {
	for _, version := range []string{"v20", "v21"} {
		t.Run(version, func(t *testing.T) {
			source := originalViewerFixture(t, version)
			dir := filepath.Join(t.TempDir(), "client")
			if err := PrepareInputClient(source, dir); err != nil {
				t.Fatal(err)
			}
			cmd := exec.Command("node", "--test", "layout_test.cjs", "viewer_test.cjs", "canvas_test.cjs")
			cmd.Env = append(os.Environ(), "FLOE_LAYOUT_FIXTURE="+dir)
			if out, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("layout and capabilities: %v\n%s", err, out)
			}
		})
	}
}
