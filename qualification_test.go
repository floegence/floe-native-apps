package nativeapps

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestQualificationRetriesAcquisitionOnly(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX qualification script")
	}
	for _, mode := range []string{"transient", "unavailable", "graphics"} {
		t.Run(mode, func(t *testing.T) {
			dir := t.TempDir()
			for name, script := range map[string]string{
				"native-check": "#!/bin/sh\nexit 0\n",
				"sleep":        "#!/bin/sh\nprintf '%s\\n' \"$1\" >> \"$FLOE_QUALIFICATION_TEST_DIR/delays\"\n",
				"docker": `#!/bin/sh
case "$1" in
  pull)
    path="$FLOE_QUALIFICATION_TEST_DIR/attempts"
    n=0; if test -f "$path"; then n=$(cat "$path"); fi
    n=$((n+1)); echo "$n" > "$path"
    test "$FLOE_QUALIFICATION_TEST_MODE" != unavailable && test "$n" -gt 2
    ;;
  run)
    echo run >> "$FLOE_QUALIFICATION_TEST_DIR/runs"
    test "$FLOE_QUALIFICATION_TEST_MODE" != graphics
    ;;
  image) echo 'fixture-digest' ;;
  *) exit 2 ;;
esac
`,
			} {
				if err := os.WriteFile(filepath.Join(dir, name), []byte(script), 0700); err != nil {
					t.Fatal(err)
				}
			}
			cmd := exec.Command("sh", "scripts/qualify.sh")
			cmd.Env = append(os.Environ(), "PATH="+dir+":"+os.Getenv("PATH"), "NATIVE_CHECK="+filepath.Join(dir, "native-check"), "NATIVE_ROOT="+dir, "NATIVE_ARCH=arm64", "FLOE_QUALIFICATION_TEST_DIR="+dir, "FLOE_QUALIFICATION_TEST_MODE="+mode)
			out, err := cmd.CombinedOutput()
			if (err == nil) != (mode == "transient") {
				t.Fatalf("qualification %s: %v %s", mode, err, out)
			}
			delays, _ := os.ReadFile(filepath.Join(dir, "delays"))
			runs, _ := os.ReadFile(filepath.Join(dir, "runs"))
			wantDelays, wantRuns := "10\n20\n", 2
			if mode == "unavailable" {
				wantDelays, wantRuns = "10\n20\n40\n", 0
			}
			if mode == "graphics" {
				wantRuns = 1
			}
			if string(delays) != wantDelays || strings.Count(string(runs), "run\n") != wantRuns {
				t.Fatalf("incorrect retry boundary: delays=%q runs=%q", delays, runs)
			}
		})
	}
}
