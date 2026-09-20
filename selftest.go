package nativeapps

import (
	"context"
	_ "embed"
	"fmt"
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
	quote := func(value string) string { return "'" + strings.ReplaceAll(value, "'", "'\\''") + "'" }
	args := []string{"--", t.Xpra, "start", "--daemon=no", "--systemd-run=no", "--attach=no", "--use-display=no", "--html=no", "--source=", "--source-start=", "--input-method=none", "--socket-dir=" + state, "--socket-dirs=" + state, "--sessions-dir=" + filepath.Join(state, "sessions"), "--exit-with-windows=yes", "--start-child=" + quote(t.Python) + " " + quote(launcher) + " " + quote(t.Python) + " " + quote(fixture) + " " + quote(state), "--xvfb=" + quote(t.Xvfb) + " -screen 0 1024x768x24 -nolisten tcp -noreset +extension Composite -auth $XAUTHORITY", "--notifications=no", "--mdns=no", "--pulseaudio=no", "--speaker=off", "--microphone=off", "--webcam=no", "--printing=no", "--dbus-launch=", "--start-new-commands=no", "--opengl=no"}
	command := exec.Command(t.DBus, args...)
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
		child := exec.CommandContext(ctx, t.Xpra, args...)
		child.Env = clean
		return child.Output()
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
		if err == nil && strings.Contains(string(info), "state.windows=1") {
			break
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
	probe := exec.CommandContext(ctx, t.Python, "-c", nativeInputProbe, state, strings.TrimPrefix(address, "socket://"))
	probe.Env = clean
	if out, err := probe.CombinedOutput(); err != nil {
		return fmt.Errorf("native picture and input check: %w (%s)", err, string(out))
	}
	return nil
}

const nativeFixture = `import gi, json, os, pathlib, sys
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
root=pathlib.Path(sys.argv[1])
window=Gtk.Window(title="Native graphics check")
button=Gtk.Button(label="Native display and input")
window.add(button)
window.set_default_size(480,240)
window.connect("destroy",Gtk.main_quit)
receipt={"display":os.environ["DISPLAY"],"xauthority":os.environ.get("XAUTHORITY",""),"input":False}
def save():
    temporary=root/"receipt.tmp"
    temporary.write_text(json.dumps(receipt))
    temporary.replace(root/"receipt.json")
def clicked(*args):
    receipt["input"]=True
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
