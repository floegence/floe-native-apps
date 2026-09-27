//go:build linux

package nativeapps

import (
	"bytes"
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"image/png"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

//go:embed selfcheck/desktop.py
var desktopCheckApplication []byte

// DesktopSelfTest executes only an installation-owned GTK fixture. It requires
// decoded pixels, actual Unicode/Enter document bytes, reconnect and normal exit.
// It does not establish Snap/Flatpak or desktop-environment support by itself.
func DesktopSelfTest(ctx context.Context, root, architecture string) error {
	pkg, err := DesktopForPlatform("linux", architecture)
	if err != nil {
		return err
	}
	return desktopSelfTest(ctx, root, pkg)
}

func desktopSelfTest(parent context.Context, root string, pkg Package) (result error) {
	return runDesktopSelfTest(parent, root, pkg, nil)
}

// retain is used only by explicit qualification to keep fixture-owned pixels
// and receipts. Production installation never records document content.
func runDesktopSelfTest(parent context.Context, root string, pkg Package, retain func(string, []byte) error) (result error) {
	ctx, cancel := context.WithTimeout(parent, 60*time.Second)
	defer cancel()
	tools, err := ResolveDesktopTools(root, pkg.Architecture)
	if err != nil {
		return err
	}
	state, err := os.MkdirTemp("", "floe-desktop-check-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(state)
	runtime := filepath.Join(state, "runtime")
	if err := os.Mkdir(runtime, 0700); err != nil {
		return err
	}
	application := filepath.Join(state, "fixture.py")
	if err := os.WriteFile(application, desktopCheckApplication, 0600); err != nil {
		return err
	}
	quote := func(value string) string { return "'" + strings.ReplaceAll(value, "'", "'\"'\"'") + "'" }
	receipt := filepath.Join(state, "document.json")
	entry := filepath.Join(state, "application")
	launcher := "#!/bin/sh\nexec " + quote(tools.Python) + " " + quote(application) + " " + quote(receipt) + " 2>" + quote(filepath.Join(state, "application.log")) + "\n"
	if err := os.WriteFile(entry, []byte(launcher), 0700); err != nil {
		return err
	}
	desktop := filepath.Join(state, "fixture.desktop")
	// The random fixture path is escaped using Desktop Entry quoted-argument rules.
	executable := strings.NewReplacer("\\", "\\\\", "\"", "\\\"", "`", "\\`", "$", "\\$", "%", "%%").Replace(entry)
	if err := os.WriteFile(desktop, []byte("[Desktop Entry]\nType=Application\nName=Native installation check\nExec=\""+executable+"\"\nTerminal=false\n"), 0600); err != nil {
		return err
	}
	var environment []string
	for _, name := range []string{"home", "config", "cache", "data"} {
		if err := os.Mkdir(filepath.Join(state, name), 0700); err != nil {
			return err
		}
	}
	for _, item := range os.Environ() {
		key, _, _ := strings.Cut(item, "=")
		switch key {
		case "HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_DATA_DIRS", "GDK_PIXBUF_MODULE_FILE", "GDK_PIXBUF_MODULEDIR", "FONTCONFIG_PATH", "FONTCONFIG_FILE", "DISPLAY", "WAYLAND_DISPLAY", "WAYLAND_SOCKET", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "GDK_BACKEND", "GTK_IM_MODULE", "GTK_IM_MODULE_FILE", "GTK_PATH", "GIO_EXTRA_MODULES", "PYTHONPATH", "PYTHONHOME", "LD_PRELOAD", "LD_LIBRARY_PATH", "GSETTINGS_SCHEMA_DIR", "GSETTINGS_BACKEND":
			continue
		}
		environment = append(environment, item)
	}
	environment = append(environment, "GDK_BACKEND=wayland", "XDG_RUNTIME_DIR="+runtime, "GSETTINGS_BACKEND=memory",
		"HOME="+filepath.Join(state, "home"), "XDG_CONFIG_HOME="+filepath.Join(state, "config"), "XDG_CACHE_HOME="+filepath.Join(state, "cache"), "XDG_DATA_HOME="+filepath.Join(state, "data"),
		"GSETTINGS_SCHEMA_DIR="+filepath.Join(state, "instance", "desktop-services"),
		"GDK_PIXBUF_MODULE_FILE="+filepath.Join(state, "instance", "desktop-services", "pixbuf.loaders"),
		"GDK_PIXBUF_MODULEDIR="+filepath.Join(root, "usr/lib/gdk-pixbuf-2.0/2.10.0/loaders"),
		"XDG_DATA_DIRS="+filepath.Join(state, "instance", "desktop-services", "share")+":"+filepath.Join(root, "usr/share"),
		"FONTCONFIG_FILE="+filepath.Join(state, "instance", "desktop-services", "fonts.conf"),
		"GTK_IM_MODULE_FILE="+filepath.Join(state, "instance", "desktop-services", "gtk.immodules"))
	plan, err := PlanApplication(ctx, ApplicationPlanOptions{Python: tools.Python, Environment: tools.Environment(environment),
		DesktopFile: desktop, Backends: []BackendCapability{desktopBackend(pkg.Digest())}})
	if err != nil {
		return err
	}
	prepared, err := prepareDesktopSession(ctx, root, pkg.Architecture, pkg.Digest(), DesktopSessionOptions{
		Directory: filepath.Join(state, "instance"), Runtime: runtime, Instance: "installation-check", Plan: plan, Environment: environment})
	if err != nil {
		return err
	}
	command := exec.Command(prepared.Executable, prepared.Configuration)
	command.Env = environment
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	log, err := os.OpenFile(filepath.Join(state, "check.log"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	defer log.Close()
	command.Stdout, command.Stderr = log, log
	if err := command.Start(); err != nil {
		return err
	}
	done := make(chan error, 1)
	go func() { done <- command.Wait() }()
	finished := false
	defer func() {
		if !finished {
			// Only this disposable check's new process group is terminated. There
			// is no application selected by a user in an installation self-check.
			_ = syscall.Kill(-command.Process.Pid, syscall.SIGTERM)
			select {
			case <-done:
			case <-time.After(3 * time.Second):
				_ = syscall.Kill(-command.Process.Pid, syscall.SIGKILL)
				<-done
			}
		}
		if result != nil {
			data, _ := os.ReadFile(filepath.Join(state, "check.log"))
			if len(data) > 16384 {
				data = data[len(data)-16384:]
			}
			status, _ := os.ReadFile(filepath.Join(state, "instance", "desktop-status.json"))
			if len(status) > 16384 {
				status = status[len(status)-16384:]
			}
			applicationLog, _ := os.ReadFile(filepath.Join(state, "application.log"))
			if len(applicationLog) > 16384 {
				applicationLog = applicationLog[len(applicationLog)-16384:]
			}
			result = fmt.Errorf("desktop installation self-check: %w (%s; %s; %s)", result, data, status, applicationLog)
		}
	}()
	wait := func(test func() bool) error {
		tick := time.NewTicker(20 * time.Millisecond)
		defer tick.Stop()
		for {
			if test() {
				return nil
			}
			select {
			case <-ctx.Done():
				return ctx.Err()
			case err := <-done:
				finished = true
				return fmt.Errorf("desktop fixture ended before receipt: %v", err)
			case <-tick.C:
			}
		}
	}
	if err := wait(func() bool { _, err := os.Lstat(prepared.Endpoint.SocketPath); return err == nil }); err != nil {
		return err
	}
	client, _, err := DialDesktop(ctx, prepared.Endpoint)
	if err != nil {
		return err
	}
	defer func() {
		if client != nil {
			_ = client.Close()
		}
	}()
	// A response may arrive after a frame. Keep only the latest unacknowledged
	// frame, since a native target change may supersede it while input is pending.
	var pending *DesktopEvent
	readResponse := func(id uint64) (DesktopEvent, error) {
		for {
			event, err := client.Read(ctx)
			if err != nil {
				return event, err
			}
			if event.Frame != nil {
				pending = &event
			}
			if event.ID == id {
				if event.Error != "" {
					return event, errors.New(event.Error)
				}
				return event, nil
			}
		}
	}
	paint := func(stage string) (DesktopFrame, error) {
		for {
			var event DesktopEvent
			var err error
			if pending != nil {
				event, pending = *pending, nil
			} else {
				event, err = client.Read(ctx)
			}
			if err != nil {
				return DesktopFrame{}, err
			}
			if event.Frame == nil {
				continue
			}
			frame := *event.Frame
			image, err := png.Decode(bytes.NewReader(event.Pixels))
			if err != nil {
				return frame, err
			}
			pixels := 0
			for y := image.Bounds().Min.Y; y < image.Bounds().Max.Y; y++ {
				for x := image.Bounds().Min.X; x < image.Bounds().Max.X; x++ {
					r, g, b, _ := image.At(x, y).RGBA()
					if r>>8 == 19 && g>>8 == 87 && b>>8 == 155 {
						pixels++
					}
				}
			}
			id, err := client.Send(ctx, DesktopRequest{Method: "frame_ack", Frame: frame.Sequence})
			if err != nil {
				return frame, err
			}
			response, err := readResponse(id)
			if err != nil {
				if response.Error == "FRAME_TARGET_UNAVAILABLE" {
					continue
				}
				return frame, err
			}
			if pixels >= 100 {
				if retain != nil {
					if err := retain(stage+".png", event.Pixels); err != nil {
						return frame, err
					}
					data, err := json.Marshal(frame)
					if err != nil {
						return frame, err
					}
					if err := retain(stage+".json", data); err != nil {
						return frame, err
					}
				}
				return frame, nil
			}
		}
	}
	frame, err := paint("first-frame")
	if err != nil {
		return err
	}
	input := func(operation any) error {
		data, _ := json.Marshal(operation)
		id, err := client.Send(ctx, DesktopRequest{Method: "input", Connection: client.Connection(), Window: frame.Window, Generation: frame.Generation, Operation: data})
		if err != nil {
			return err
		}
		_, err = readResponse(id)
		return err
	}
	for _, pressed := range []bool{true, false} {
		if err := input(map[string]any{"kind": "button", "button": 0, "pressed": pressed, "x": 250, "y": 180}); err != nil {
			return err
		}
	}
	text := "Native 中文 日本語 한국어 😀 𠀀 e\u0301 🧑🏽\u200d💻"
	for range 2 {
		if err := input(map[string]any{"kind": "text", "text": text}); err != nil {
			return err
		}
		for _, pressed := range []bool{true, false} {
			if err := input(map[string]any{"kind": "key", "code": 28, "pressed": pressed}); err != nil {
				return err
			}
		}
	}
	expected := strings.Repeat(text+"\n", 2)
	type document struct {
		PID  int    `json:"pid"`
		Text string `json:"text"`
	}
	var first document
	if err := wait(func() bool {
		data, err := os.ReadFile(receipt)
		return err == nil && json.Unmarshal(data, &first) == nil && first.Text == expected
	}); err != nil {
		return err
	}
	old := client.Connection()
	_ = client.Close()
	pending = nil
	client, _, err = DialDesktop(ctx, prepared.Endpoint)
	if err != nil {
		return err
	}
	if client.Connection() <= old {
		return errors.New("desktop generation did not advance")
	}
	frame, err = paint("reattached-frame")
	if err != nil {
		return err
	}
	for _, pressed := range []bool{true, false} {
		if err := input(map[string]any{"kind": "key", "code": 48, "pressed": pressed}); err != nil {
			return err
		}
	}
	if err := wait(func() bool {
		data, err := os.ReadFile(receipt)
		var current document
		return err == nil && json.Unmarshal(data, &current) == nil && current.PID == first.PID && current.Text == expected+"b"
	}); err != nil {
		return err
	}
	if retain != nil {
		data, err := os.ReadFile(receipt)
		if err != nil {
			return err
		}
		if err := retain("document.json", data); err != nil {
			return err
		}
	}
	id, err := client.Send(ctx, DesktopRequest{Method: "close_window", Window: frame.Window})
	if err != nil {
		return err
	}
	if _, err := readResponse(id); err != nil {
		return err
	}
	select {
	case err := <-done:
		finished = true
		if err != nil {
			return err
		}
	case <-ctx.Done():
		return ctx.Err()
	}
	data, err := os.ReadFile(filepath.Join(state, "instance", "application.json"))
	if err != nil {
		return err
	}
	var exit struct {
		State      string `json:"state"`
		ExitCode   int    `json:"exit_code"`
		Terminated bool   `json:"termination_requested"`
	}
	if json.Unmarshal(data, &exit) != nil || exit.State != "exited" || exit.ExitCode != 0 || exit.Terminated {
		return errors.New("desktop fixture did not close normally")
	}
	if retain != nil {
		if err := retain("application.json", data); err != nil {
			return err
		}
		data, err = os.ReadFile(filepath.Join(state, "instance", "desktop-status.json"))
		if err != nil {
			return err
		}
		if err := retain("desktop-status.json", data); err != nil {
			return err
		}
	}
	return nil
}
