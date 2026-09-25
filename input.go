package nativeapps

import (
	"bufio"
	"context"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
)

// ClientInputVersion is the confirmed-text protocol advertised on Xpra's
// authenticated connection. Consumers must reject sessions without this version.
const ClientInputVersion = 1

// ClientInputTextLimit bounds one atomic text commit in UTF-8 bytes. Applications
// must report rejected commits; truncation and automatic retry are forbidden.
const ClientInputTextLimit = 16000

//go:embed input_xpra.py input_dispatch.py input_order.py input_xim.py input_context.py input_probe.py display.py input_modules/dist
var inputSources embed.FS

// ClientInput is prepared support for one private X11 application session. Launch
// Launcher with the probed Xpra Python interpreter, normal Xpra arguments and
// XpraArgs. Start a private session bus first and pass its address explicitly to
// the application's start environment. These paths contain first-party adapters,
// never a copied host toolkit or a second input method engine.
type ClientInput struct {
	Launcher, GTKModules, QtPlugins string
	// GTKPath is a private module root. Only GTK4 scans its 4.0.0 ABI directory;
	// GTK3 uses GTKModules and must never load the GTK4 shared object.
	GTKPath string
}

// PrepareClientInput installs the complete input contract into a new absolute
// private directory. The architecture is the application host's Go architecture.
func PrepareClientInput(directory, architecture string) (ClientInput, error) {
	if !filepath.IsAbs(directory) {
		return ClientInput{}, ErrInvalid
	}
	if architecture != "amd64" && architecture != "arm64" {
		return ClientInput{}, ErrUnsupported
	}
	if err := os.Mkdir(directory, 0700); err != nil {
		return ClientInput{}, err
	}
	complete := false
	defer func() {
		if !complete {
			_ = os.RemoveAll(directory)
		}
	}()
	for _, name := range []string{"input_xpra.py", "input_dispatch.py", "input_order.py", "input_xim.py", "input_context.py", "display.py"} {
		data, err := inputSources.ReadFile(name)
		if err != nil {
			return ClientInput{}, err
		}
		if err := os.WriteFile(filepath.Join(directory, name), data, 0600); err != nil {
			return ClientInput{}, err
		}
	}
	plugins := filepath.Join(directory, "qt", "platforminputcontexts")
	if err := os.MkdirAll(plugins, 0700); err != nil {
		return ClientInput{}, err
	}
	gtkPath := filepath.Join(directory, "gtk")
	gtk4 := filepath.Join(gtkPath, "4.0.0", "immodules")
	if err := os.MkdirAll(gtk4, 0700); err != nil {
		return ClientInput{}, err
	}
	for _, name := range []string{"libfloe-gtk3.so", "libfloe-gtk4.so", "libfloe-qt5.so", "libfloe-qt6.so"} {
		data, err := inputSources.ReadFile("input_modules/dist/" + architecture + "/" + name)
		if err != nil {
			return ClientInput{}, fmt.Errorf("client input module unavailable: %w", err)
		}
		path := filepath.Join(plugins, name)
		if name == "libfloe-gtk3.so" {
			path = filepath.Join(directory, name)
		}
		if name == "libfloe-gtk4.so" {
			path = filepath.Join(gtk4, name)
		}
		if err := os.WriteFile(path, data, 0600); err != nil {
			return ClientInput{}, err
		}
	}
	cache := filepath.Join(directory, "gtk.immodules")
	if err := os.WriteFile(cache, []byte(strconv.Quote(filepath.Join(directory, "libfloe-gtk3.so"))+"\n\"floe-client\" \"Client confirmed text\" \"\" \"\" \"\"\n"), 0600); err != nil {
		return ClientInput{}, err
	}
	complete = true
	return ClientInput{Launcher: filepath.Join(directory, "input_xpra.py"), GTKModules: cache, GTKPath: gtkPath, QtPlugins: filepath.Dir(plugins)}, nil
}

// XpraArgs selects client-owned composition in the private application
// environment. No host desktop input configuration is inherited or modified.
func (input ClientInput) XpraArgs(baseEnvironment []string) []string {
	qtPlugins := input.QtPlugins
	for _, item := range baseEnvironment {
		if value, ok := strings.CutPrefix(item, "QT_PLUGIN_PATH="); ok && value != "" {
			qtPlugins += ":" + value
		}
	}
	return []string{"--input-method=keep", "--start-env=GTK_IM_MODULE=floe-client", "--start-env=QT_IM_MODULE=floe-client", "--start-env=IBUS_ADDRESS=", "--start-env=IBUS_ADDRESS_FILE=", "--start-env=XMODIFIERS=@im=floe-client", "--start-env=GDK_BACKEND=x11", "--start-env=QT_QPA_PLATFORM=xcb", "--start-env=GDK_CORE_DEVICE_EVENTS=1", "--start-env=QT_XCB_NO_XI2=1", "--start-env=GTK_IM_MODULE_FILE=" + input.GTKModules, "--start-env=GTK_PATH=" + input.GTKPath, "--start-env=FLOE_NATIVE_INPUT_GTK_PATH=" + input.GTKPath, "--start-env=QT_PLUGIN_PATH=" + qtPlugins}
}

type ClientInputCapability struct {
	Version     int    `json:"version"`
	XpraVersion string `json:"xpra_version"`
}

// ProbeClientInput checks the interpreter and original XIM library before any
// application is launched. It does not acquire components or require a display.
// A successful probe does not assert that a particular application has a focused
// editable input context; that boundary is checked for every submitted commit.
func ProbeClientInput(ctx context.Context, python string, environment []string) (ClientInputCapability, error) {
	if !filepath.IsAbs(python) {
		return ClientInputCapability{}, ErrInvalid
	}
	probe, _ := inputSources.ReadFile("input_probe.py")
	command := exec.CommandContext(ctx, python, "-c", string(probe))
	command.Env = environment
	data, err := command.Output()
	if err != nil {
		return ClientInputCapability{}, fmt.Errorf("client input support unavailable: %w", err)
	}
	var result ClientInputCapability
	if json.Unmarshal(data, &result) != nil || result.Version != ClientInputVersion || result.XpraVersion == "" {
		return ClientInputCapability{}, errors.New("unsupported client input capability")
	}
	return result, nil
}

// SystemClientInputPython resolves the interpreter selected by an installed
// Xpra script, then probes that exact interpreter. It never substitutes the
// host's unrelated desktop Python or installs missing system dependencies.
func SystemClientInputPython(ctx context.Context, xpra string, environment []string) (string, error) {
	if !filepath.IsAbs(xpra) {
		return "", ErrInvalid
	}
	file, err := os.Open(xpra)
	if err != nil {
		return "", err
	}
	line, err := bufio.NewReader(io.LimitReader(file, 4096)).ReadString('\n')
	_ = file.Close()
	if err != nil || !strings.HasPrefix(line, "#!") {
		return "", errors.New("installed Xpra interpreter is unavailable")
	}
	parts := strings.Fields(strings.TrimPrefix(line, "#!"))
	if len(parts) == 0 {
		return "", ErrInvalid
	}
	python := parts[0]
	if filepath.Base(python) == "env" {
		if len(parts) != 2 || !strings.HasPrefix(parts[1], "python") {
			return "", ErrUnsupported
		}
		python = ""
		for _, item := range environment {
			value, ok := strings.CutPrefix(item, "PATH=")
			if !ok {
				continue
			}
			for _, directory := range filepath.SplitList(value) {
				if !filepath.IsAbs(directory) {
					continue
				}
				candidate := filepath.Join(directory, parts[1])
				if info, err := os.Stat(candidate); err == nil && info.Mode().IsRegular() && info.Mode().Perm()&0111 != 0 {
					python = candidate
					break
				}
			}
		}
	} else if len(parts) != 1 {
		return "", ErrUnsupported
	}
	if !filepath.IsAbs(python) || !strings.HasPrefix(filepath.Base(python), "python") {
		return "", ErrUnsupported
	}
	if _, err := ProbeClientInput(ctx, python, environment); err != nil {
		return "", err
	}
	return python, nil
}
