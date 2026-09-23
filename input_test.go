package nativeapps

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestSystemInputUsesTheInstalledXpraInterpreter(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("system Xpra interpreter discovery targets Linux")
	}
	directory := t.TempDir()
	python := filepath.Join(directory, "python3")
	if err := os.WriteFile(python, []byte("#!/bin/sh\nprintf '%s\\n' '{\"version\":1,\"xpra_version\":\"6.2.2\"}'\n"), 0700); err != nil {
		t.Fatal(err)
	}
	xpra := filepath.Join(directory, "xpra")
	for _, shebang := range []string{"#!" + python, "#!/usr/bin/env python3"} {
		if err := os.WriteFile(xpra, []byte(shebang+"\n"), 0700); err != nil {
			t.Fatal(err)
		}
		got, err := SystemClientInputPython(context.Background(), xpra, []string{"PATH=relative:" + directory})
		if err != nil || got != python {
			t.Fatalf("installed interpreter: got %q, %v", got, err)
		}
	}
	for _, shebang := range []string{"#!/usr/bin/env python3 -E", "#!" + python + " -E", "#!/bin/sh", "not a script"} {
		if err := os.WriteFile(xpra, []byte(shebang+"\n"), 0700); err != nil {
			t.Fatal(err)
		}
		if _, err := SystemClientInputPython(context.Background(), xpra, []string{"PATH=" + directory}); err == nil {
			t.Fatalf("unsupported interpreter accepted: %s", shebang)
		}
	}
}

func TestInputLauncherOwnsPrivateEnvironment(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "input")
	input, err := PrepareClientInput(dir, "amd64")
	if err != nil {
		t.Fatal(err)
	}
	if !filepath.IsAbs(input.Launcher) {
		t.Fatal("launcher must have an absolute path")
	}
	for _, name := range []string{"input_xpra.py", "input_dispatch.py", "input_xim.py", "input_context.py"} {
		if data, err := os.ReadFile(filepath.Join(dir, name)); err != nil || len(data) == 0 {
			t.Fatal(name, err)
		}
	}
	args := input.XpraArgs([]string{"GTK_IM_MODULE=ibus", "QT_PLUGIN_PATH=/application/plugins"})
	joined := strings.Join(args, "\n")
	for _, value := range []string{"--input-method=keep", "--start-env=GTK_IM_MODULE=floe-client", "--start-env=QT_IM_MODULE=floe-client", "--start-env=GDK_CORE_DEVICE_EVENTS=1", "--start-env=QT_XCB_NO_XI2=1", "--start-env=XMODIFIERS=@im=floe-client", "--start-env=QT_PLUGIN_PATH=" + input.QtPlugins + ":/application/plugins", "--start-env=GTK_IM_MODULE_FILE=" + input.GTKModules} {
		if !strings.Contains(joined, value) {
			t.Fatalf("missing private input setting %s", value)
		}
	}
	if _, err := PrepareClientInput("relative", "amd64"); err != ErrInvalid {
		t.Fatal(err)
	}
	if _, err := PrepareClientInput(dir, "amd64"); err == nil {
		t.Fatal("existing session was overwritten")
	}
}
