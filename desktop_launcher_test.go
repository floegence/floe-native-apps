package nativeapps

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
)

func TestDesktopLauncherSnapshot(t *testing.T) {
	directory := filepath.Join(t.TempDir(), "helper")
	entry, err := WriteDesktopLauncher(directory)
	if err != nil {
		t.Fatal(err)
	}
	before, err := os.ReadFile(entry)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := WriteDesktopLauncher(directory); err == nil {
		t.Fatal("running helper source snapshot was overwritten")
	}
	after, err := os.ReadFile(entry)
	if err != nil || !bytes.Equal(before, after) {
		t.Fatalf("existing source changed: %v", err)
	}
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("Python source import validation requires python3")
	}
	// Parse lazy imports too: native GI/IBus are intentionally loaded after the
	// private configuration is validated, but every first-party dependency must
	// already belong to this immutable source snapshot.
	command := exec.Command(python, "-c", `
import ast, pathlib, sys
root=pathlib.Path(sys.argv[1])
for path in root.glob('*.py'):
    assert not path.name.endswith('_test.py')
    for node in ast.walk(ast.parse(path.read_text())):
        names=[node.module] if isinstance(node, ast.ImportFrom) else [n.name for n in node.names] if isinstance(node, ast.Import) else []
        for name in names:
            if name and (name.startswith(('desktop_', 'application_', 'input_')) or name == 'launch_plan'):
                assert (root/(name+'.py')).is_file(), name
sys.path.insert(0,str(root))
import desktop_bootstrap
`, directory)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("installed helper import closure: %v\n%s", err, output)
	}
}

func TestDesktopLauncherRejectsRelativeDirectory(t *testing.T) {
	if _, err := WriteDesktopLauncher("relative"); err == nil {
		t.Fatal("relative helper source directory accepted")
	}
}

// This explicit native installation fixture retains its source snapshot so the
// separate graphical probes can execute precisely the Go-installed entrypoint.
// It does not install/activate native components or certify a platform itself.
func TestNativeDesktopLauncherInstallation(t *testing.T) {
	directory := os.Getenv("FLOE_TEST_DESKTOP_LAUNCHER")
	if directory == "" {
		t.Skip("explicit native helper installation fixture")
	}
	if runtime.GOOS != "linux" || !filepath.IsAbs(directory) {
		t.Fatal("absolute native Linux helper fixture directory required")
	}
	entry, err := WriteDesktopLauncher(directory)
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("installed first-party helper source: %s", entry)
}
