package nativeapps

import (
	"os"
	"path/filepath"
	"slices"
	"testing"
)

func TestApplicationLifetimeIsIndependentOfWindowsAndViewers(t *testing.T) {
	args := XpraApplicationLifetimeArgs()
	for _, required := range []string{"--exit-with-client=no", "--exit-with-windows=no", "--exit-with-children=yes"} {
		if !slices.Contains(args, required) {
			t.Fatalf("application lifetime is not authoritative: missing %s", required)
		}
	}
}

func TestApplicationLauncherRequiresPrivateAbsolutePlacement(t *testing.T) {
	if _, err := WriteApplicationLauncher("relative"); err == nil {
		t.Fatal("relative launcher placement accepted")
	}
	dir := t.TempDir()
	path, err := WriteApplicationLauncher(dir)
	if err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil || filepath.Dir(path) != dir || info.Mode().Perm() != 0600 {
		t.Fatalf("launcher was not privately placed: %v %v", info, err)
	}
}
