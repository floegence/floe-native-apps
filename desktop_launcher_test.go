package nativeapps

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestDesktopLauncherSnapshot(t *testing.T) {
	directory := filepath.Join(t.TempDir(), "helper")
	component := desktopLauncherComponent(t)
	entry, err := WriteDesktopLauncher(directory, component, "arm64")
	if err != nil {
		t.Fatal(err)
	}
	before, err := os.ReadFile(entry)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := WriteDesktopLauncher(directory, component, "arm64"); err == nil {
		t.Fatal("running helper source snapshot was overwritten")
	}
	after, err := os.ReadFile(entry)
	if err != nil || !bytes.Equal(before, after) {
		t.Fatalf("existing source changed: %v", err)
	}
	// Execute the wrapper against a task-owned recording loader. This proves
	// argument boundaries and environment isolation even on non-Linux hosts.
	loader := filepath.Join(component, "lib/ld-musl-aarch64.so.1")
	if err := os.WriteFile(loader, []byte("#!/bin/sh\nprintf '%s\\n' \"$@\" \"$PYTHONHOME\" \"${PYTHONPATH-unset}\"\n"), 0700); err != nil {
		t.Fatal(err)
	}
	configuration := filepath.Join(directory, "quoted ' configuration.json")
	probe := exec.Command(entry, configuration)
	probe.Env = append(os.Environ(), "PYTHONPATH=/host/inherited/python")
	output, err := probe.CombinedOutput()
	canonical, pathErr := filepath.EvalSymlinks(component)
	if pathErr != nil {
		t.Fatal(pathErr)
	}
	want := strings.Join([]string{"--library-path", filepath.Join(canonical, "lib") + ":" + filepath.Join(canonical, "usr/lib"),
		filepath.Join(canonical, "usr/bin/python3"), filepath.Join(directory, "desktop_bootstrap.py"),
		configuration, filepath.Join(canonical, "usr"), "unset", ""}, "\n")
	if err != nil || string(output) != want {
		t.Fatalf("private launcher arguments/environment changed: %v\n%s", err, output)
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

func desktopLauncherComponent(t *testing.T) string {
	t.Helper()
	root := filepath.Join(t.TempDir(), "component with ' quote")
	for _, name := range []string{"lib/ld-musl-aarch64.so.1", "usr/bin/python3", "usr/libexec/gio-launch-desktop"} {
		path := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("native fixture executable"), 0700); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func TestDesktopLauncherRejectsRelativeDirectory(t *testing.T) {
	if _, err := WriteDesktopLauncher("relative", desktopLauncherComponent(t), "arm64"); err == nil {
		t.Fatal("relative helper source directory accepted")
	}
}

func TestDesktopLauncherRejectsEscapedComponentExecutable(t *testing.T) {
	component, directory := desktopLauncherComponent(t), filepath.Join(t.TempDir(), "helper")
	binary := filepath.Join(component, "usr/bin/python3")
	if err := os.Remove(binary); err != nil {
		t.Fatal(err)
	}
	outside := filepath.Join(t.TempDir(), "python3")
	if err := os.WriteFile(outside, []byte("unverified"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, binary); err != nil {
		t.Fatal(err)
	}
	if _, err := WriteDesktopLauncher(directory, component, "arm64"); err == nil {
		t.Fatal("component executable escaped its verified root")
	}
	if _, err := os.Stat(directory); !os.IsNotExist(err) {
		t.Fatal("invalid resources created a helper snapshot")
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
	entry, err := WriteDesktopLauncher(directory, os.Getenv("FLOE_TEST_DESKTOP_COMPONENT"), runtime.GOARCH)
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("installed first-party helper source: %s", entry)
}
