//go:build linux

package nativeapps

import (
	"context"
	_ "embed"
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

// SelfTest creates only its own virtual display and transient GTK fixture. A
// successful check proves actual window discovery, rendered pixels, and input;
// importing a module or starting a process alone is not readiness evidence.
func SelfTest(parent context.Context, root string) (result error) {
	t, err := ResolveTools(root)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(parent, 45*time.Second)
	defer cancel()
	state, err := os.MkdirTemp("", "floe-native-check-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(state)
	if err = os.Mkdir(filepath.Join(state, "xpra"), 0700); err != nil {
		return err
	}
	fixture := filepath.Join(state, "fixture.py")
	if err = os.WriteFile(fixture, []byte(nativeFixture), 0600); err != nil {
		return err
	}
	launcher := filepath.Join(state, "launch.py")
	if err = os.WriteFile(launcher, []byte(nativeLaunchProbe), 0600); err != nil {
		return err
	}
	env := t.Environment(os.Environ())
	var clean []string
	for _, item := range env {
		key, _, _ := strings.Cut(item, "=")
		switch key {
		case "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XPRA_DEFAULT_CONF_DIRS", "XPRA_SYSTEM_CONF_DIRS", "XPRA_USER_CONF_DIRS":
			continue
		}
		clean = append(clean, item)
	}
	clean = append(clean, "XPRA_PRIVATE_XAUTH=1", "XPRA_SHARED_XAUTHORITY=0", "XPRA_DEFAULT_CONF_DIRS=", "XPRA_SYSTEM_CONF_DIRS=", "XPRA_USER_CONF_DIRS=", "XDG_RUNTIME_DIR="+state, "GDK_BACKEND=x11", "QT_QPA_PLATFORM=xcb")
	decoder := exec.CommandContext(ctx, t.Python, "-c", nativeWebSocketProbe)
	decoder.Env = clean
	if out, err := decoder.CombinedOutput(); err != nil {
		return fmt.Errorf("native WebSocket decoder check: %w (%s)", err, out)
	}
	applicationLauncher, err := WriteApplicationLauncher(state)
	if err != nil {
		return err
	}
	lifetime := exec.CommandContext(ctx, t.Python, "-c", nativeApplicationProbe, t.Python, applicationLauncher)
	lifetime.Env = clean
	if out, err := lifetime.CombinedOutput(); err != nil {
		return fmt.Errorf("native application lifetime check: %w (%s)", err, out)
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return err
	}
	websocketAddress := listener.Addr().String()
	_ = listener.Close()
	quote := func(value string) string { return "'" + strings.ReplaceAll(value, "'", "'\\''") + "'" }
	args := []string{"--", t.Xpra, "start", "--daemon=no", "--systemd-run=no", "--attach=no", "--use-display=no", "--html=no", "--source=", "--source-start=", "--input-method=none", "--socket-dir=" + state, "--socket-dirs=" + state, "--sessions-dir=" + filepath.Join(state, "sessions"), "--exit-with-windows=yes", "--start-child=" + quote(t.Python) + " " + quote(launcher) + " " + quote(t.Python) + " " + quote(fixture) + " " + quote(state), "--xvfb=" + quote(t.Xvfb) + " -screen 0 1024x768x24 -nolisten tcp -noreset +extension Composite -auth $XAUTHORITY", "--notifications=no", "--mdns=no", "--webcam=no", "--printing=no", "--dbus-launch=", "--start-new-commands=no", "--opengl=no"}
	args = append(args, XpraNoAudioArgs()...)
	args = append(args, "--bind-ws="+websocketAddress, "--exit-with-client=no")
	// Xpra intentionally discards inherited DBUS_* variables. Its child env
	// option admits only the bus this qualification process just created.
	shellArgs := []string{"--", "/bin/sh", "-c", `exec "$@" --dbus=no --dbus-control=no "--start-env=DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS"`, "native-check"}
	command := exec.Command(t.DBus, append(shellArgs, args[1:]...)...)
	command.Env = clean
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	log, err := os.OpenFile(filepath.Join(state, "check.log"), os.O_CREATE|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	defer log.Close()
	defer func() {
		if result != nil {
			if data, err := os.ReadFile(filepath.Join(state, "check.log")); err == nil {
				if len(data) > 16384 {
					data = data[len(data)-16384:]
				}
				result = fmt.Errorf("%w: %s", result, data)
			}
		}
	}()
	command.Stdout, command.Stderr = log, log
	if err = command.Start(); err != nil {
		return err
	}
	done := make(chan error, 1)
	go func() { done <- command.Wait() }()
	defer func() {
		_ = command.Process.Signal(syscall.SIGTERM)
		select {
		case <-done:
		case <-time.After(3 * time.Second):
			_ = syscall.Kill(-command.Process.Pid, syscall.SIGKILL)
			<-done
		}
	}()
	run := func(args ...string) ([]byte, error) {
		// A ready graphics server must answer inventory requests without waiting
		// for unused subsystems. Xpra 6.2 can otherwise wait five seconds on
		// every query after an unsuccessful audio initialization.
		query, cancel := context.WithTimeout(ctx, 3*time.Second)
		defer cancel()
		child := exec.CommandContext(query, t.Xpra, args...)
		child.Env = clean
		out, err := child.Output()
		if errors.Is(query.Err(), context.DeadlineExceeded) {
			return nil, fmt.Errorf("native window inventory exceeded three seconds: %w", query.Err())
		}
		return out, err
	}
	ticker := time.NewTicker(200 * time.Millisecond)
	defer ticker.Stop()
	var address string
	for address == "" {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case err := <-done:
			done <- err
			return fmt.Errorf("native display exited: %w", err)
		case <-ticker.C:
		}
		entries, _ := os.ReadDir(state)
		for _, entry := range entries {
			if entry.Type()&os.ModeSocket != 0 {
				address = "socket://" + filepath.Join(state, entry.Name())
				break
			}
		}
	}
	for {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		info, err := run("info", address)
		if errors.Is(err, context.DeadlineExceeded) {
			return err
		}
		if err == nil && strings.Contains(string(info), "state.windows=1") {
			break
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
	// Repeat the request after readiness: a completed startup must not leave
	// an unset initialization event that delays every subsequent connection.
	if _, err := run("info", address); err != nil {
		return fmt.Errorf("native ready window inventory: %w", err)
	}
	// Exercise both the local protocol and the browser's transport. Each new
	// viewer must paint and deliver input to the same application after detach.
	for _, transport := range []string{strings.TrimPrefix(address, "socket://"), "ws://" + websocketAddress, "ws://" + websocketAddress, "ws://" + websocketAddress} {
		probe := exec.CommandContext(ctx, t.Python, "-c", nativeInputProbe, state, transport)
		probe.Env = clean
		if out, err := probe.CombinedOutput(); err != nil {
			return fmt.Errorf("native picture, input and reconnect check: %w (%s)", err, string(out))
		}
		info, err := run("info", address)
		if err != nil || !strings.Contains(string(info), "state.windows=1") {
			return fmt.Errorf("native application did not survive viewer detach: %v", err)
		}
	}
	return nil
}

const nativeFixture = `import gi, json, os, pathlib, sys
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gio
assert os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "private session bus missing"
assert Gio.bus_get_sync(Gio.BusType.SESSION, None).get_unique_name(), "private session bus unavailable"
root=pathlib.Path(sys.argv[1])
window=Gtk.Window(title="Native graphics check")
button=Gtk.Button(label="Native display and input")
window.add(button)
window.set_default_size(480,240)
window.connect("destroy",Gtk.main_quit)
receipt={"display":os.environ["DISPLAY"],"xauthority":os.environ.get("XAUTHORITY",""),"pid":os.getpid(),"input_count":0}
def save():
    temporary=root/"receipt.tmp"
    temporary.write_text(json.dumps(receipt))
    temporary.replace(root/"receipt.json")
def clicked(*args):
    receipt["input_count"]+=1
    save()
button.connect("clicked",clicked)
window.show_all()
def ready():
    origin=window.get_window().get_origin()
    x,y=button.translate_coordinates(window,button.get_allocated_width()//2,button.get_allocated_height()//2)
    receipt.update(x=x+origin[-2],y=y+origin[-1])
    save()
    return False
GLib.idle_add(ready)
GLib.timeout_add_seconds(40,Gtk.main_quit)
Gtk.main()
`

//go:embed selfcheck/client.py
var nativeInputProbe string

//go:embed selfcheck/websocket.py
var nativeWebSocketProbe string

//go:embed selfcheck/application.py
var nativeApplicationProbe string

const nativeLaunchProbe = `import gi, sys
gi.require_version("Gio","2.0")
from gi.repository import Gio,GLib
def quote(value):
    for c in ("\\", '"', "\u0060", "$", "%"):
        value=value.replace(c, "%%" if c=="%" else "\\"+c)
    return '"'+value+'"'
entry=GLib.KeyFile.new()
entry.set_string("Desktop Entry","Type","Application")
entry.set_string("Desktop Entry","Name","Native launch qualification")
entry.set_string("Desktop Entry","Exec"," ".join(quote(a) for a in sys.argv[1:]))
app=Gio.DesktopAppInfo.new_from_keyfile(entry)
assert app and app.launch([],Gio.AppLaunchContext.new()), "native GIO launch failed"
`
