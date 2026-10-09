//go:build linux

package hostdesktop

import (
	"context"
	"errors"
	"io"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/godbus/dbus/v5"
)

// One connection observes logind; fresh property reads remain mandatory at the
// input boundary. Signals wake the owner, but never certify cached authority.
type loginSeatReader struct {
	bus             *dbus.Conn
	signals         chan *dbus.Signal
	compositor      map[uint32]string
	lockBindings    map[string]loginLockBinding
	readProperties  func(context.Context, dbus.ObjectPath, string) (map[string]dbus.Variant, error)
	readTerminal    func() ([]byte, error)
	bindCompositor  func(context.Context, uint32) (string, error)
	checkCompositor func(uint32, string) error
	readEnvironment func(string) ([]byte, error)
}

type loginLockBinding struct {
	session string
	path    dbus.ObjectPath
}

func openLoginSeatReader() (*loginSeatReader, error) {
	bus, err := dbus.ConnectSystemBus()
	if err != nil {
		return nil, err
	}
	r := &loginSeatReader{bus: bus, signals: make(chan *dbus.Signal, 16), compositor: map[uint32]string{}}
	r.readProperties = r.properties
	r.readTerminal = func() ([]byte, error) { return os.ReadFile("/sys/class/tty/tty0/active") }
	r.bindCompositor, r.checkCompositor = loginCompositor, loginCompositorCurrent
	r.readEnvironment = func(identity string) ([]byte, error) {
		pid, _, ok := strings.Cut(identity, ":")
		if !ok {
			return nil, errLoginCaptureUnavailable
		}
		file, err := os.Open("/proc/" + pid + "/environ")
		if err != nil {
			return nil, err
		}
		defer file.Close()
		return io.ReadAll(io.LimitReader(file, 1<<20))
	}
	bus.Signal(r.signals)
	if err := bus.AddMatchSignal(dbus.WithMatchSender("org.freedesktop.login1"), dbus.WithMatchInterface("org.freedesktop.DBus.Properties")); err != nil {
		_ = bus.Close()
		return nil, err
	}
	return r, nil
}

func (r *loginSeatReader) close() { r.bus.RemoveSignal(r.signals); _ = r.bus.Close() }

func (r *loginSeatReader) properties(ctx context.Context, path dbus.ObjectPath, iface string) (map[string]dbus.Variant, error) {
	var values map[string]dbus.Variant
	err := r.bus.Object("org.freedesktop.login1", path).CallWithContext(ctx, "org.freedesktop.DBus.Properties.GetAll", 0, iface).Store(&values)
	return values, err
}

func (r *loginSeatReader) seat(parent context.Context, seat string) (loginSeatState, error) {
	ctx, cancel := context.WithTimeout(parent, 200*time.Millisecond)
	defer cancel()
	var state loginSeatState
	if seat != "seat0" {
		return state, errLoginCaptureUnavailable
	}
	values, err := r.readProperties(ctx, "/org/freedesktop/login1/seat/seat0", "org.freedesktop.login1.Seat")
	if err != nil {
		return state, err
	}
	active, ok := values["ActiveSession"].Value().([]any)
	if !ok || len(active) != 2 {
		return state, errLoginCaptureUnavailable
	}
	id, ok := active[0].(string)
	path, pathOK := active[1].(dbus.ObjectPath)
	if !ok || !pathOK || id == "" || len(id) > 80 || strings.ContainsAny(id, " \t\n/") || !path.IsValid() || !strings.HasPrefix(string(path), "/org/freedesktop/login1/session/") {
		return state, errLoginCaptureUnavailable
	}
	values, err = r.readProperties(ctx, path, "org.freedesktop.login1.Session")
	if err != nil {
		return state, err
	}
	text := func(name string) string { value, _ := values[name].Value().(string); return value }
	vt, _ := values["VTNr"].Value().(uint32)
	enabled, _ := values["Active"].Value().(bool)
	terminal, err := r.readTerminal()
	if err != nil || vt == 0 || !enabled || strings.TrimSpace(string(terminal)) != "tty"+strconv.FormatUint(uint64(vt), 10) {
		return state, errLoginCaptureUnavailable
	}
	if text("Type") != "wayland" && text("Type") != "x11" || text("Class") != "greeter" && text("Class") != "user" {
		return state, errLoginCaptureUnavailable
	}
	switch text("Service") {
	case "gdm-password", "gdm-autologin", "gdm-launch-environment":
	default:
		return state, errLoginCaptureUnavailable
	}
	user, ok := values["User"].Value().([]any)
	if !ok || len(user) != 2 {
		return state, errLoginCaptureUnavailable
	}
	uid, ok := user[0].(uint32)
	if !ok {
		return state, errLoginCaptureUnavailable
	}
	identity := r.compositor[uid]
	if identity == "" || r.checkCompositor(uid, identity) != nil {
		identity, err = r.bindCompositor(ctx, uid)
		if err != nil {
			return state, err
		}
		r.compositor[uid] = identity
	}
	locked, ok := values["LockedHint"].Value().(bool)
	if !ok {
		return state, errors.New("lock authority unavailable")
	}
	lockSession := id
	if text("Class") == "user" {
		binding, bindErr := r.lockBinding(ctx, uid, identity, loginLockBinding{id, path})
		if bindErr != nil {
			return state, bindErr
		}
		lockSession = binding.session
		if binding.path != path {
			locker, readErr := r.readProperties(ctx, binding.path, "org.freedesktop.login1.Session")
			if readErr != nil {
				return state, readErr
			}
			user, valid := locker["User"].Value().([]any)
			if !valid || len(user) != 2 || user[0] != uid || locker["Class"].Value() != "user" || locker["Active"].Value() != true {
				return state, errLoginCaptureUnavailable
			}
			hint, valid := locker["LockedHint"].Value().(bool)
			if !valid {
				return state, errLoginCaptureUnavailable
			}
			locked = locked || hint
		}
	}
	return loginSeatState{session: id, kind: text("Class"), compositor: identity, lockSession: lockSession, uid: uid, vt: vt, locked: locked}, nil
}

// GNOME caches its logind session from XDG_SESSION_ID, or the user's Display
// when started by the user manager. That can differ from the physical seat after
// another desktop login. Keep the physical seat/VT boundary and additionally
// read the lock hint GNOME actually owns; never infer unlocked from the seat alone.
func (r *loginSeatReader) lockBinding(ctx context.Context, uid uint32, identity string, physical loginLockBinding) (loginLockBinding, error) {
	if binding, ok := r.lockBindings[identity]; ok {
		return binding, nil
	}
	if r.readEnvironment == nil {
		return loginLockBinding{}, errLoginCaptureUnavailable
	}
	environment, err := r.readEnvironment(identity)
	if err != nil {
		return loginLockBinding{}, err
	}
	id := ""
	for _, entry := range strings.Split(string(environment), "\x00") {
		if value, ok := strings.CutPrefix(entry, "XDG_SESSION_ID="); ok {
			id = value
			break
		}
	}
	binding := physical
	if id != physical.session {
		values, err := r.readProperties(ctx, dbus.ObjectPath("/org/freedesktop/login1/user/_"+strconv.FormatUint(uint64(uid), 10)), "org.freedesktop.login1.User")
		if err != nil {
			return loginLockBinding{}, err
		}
		display, ok := values["Display"].Value().([]any)
		if id == "" {
			if !ok || len(display) != 2 {
				return loginLockBinding{}, errLoginCaptureUnavailable
			}
			id, ok = display[0].(string)
			path, pathOK := display[1].(dbus.ObjectPath)
			if !ok || !pathOK {
				return loginLockBinding{}, errLoginCaptureUnavailable
			}
			binding = loginLockBinding{id, path}
		} else {
			sessions, ok := values["Sessions"].Value().([][]any)
			found := false
			for _, session := range sessions {
				if len(session) != 2 || session[0] != id {
					continue
				}
				path, valid := session[1].(dbus.ObjectPath)
				if valid {
					binding = loginLockBinding{id, path}
					found = true
				}
			}
			if !ok || !found {
				return loginLockBinding{}, errLoginCaptureUnavailable
			}
		}
	}
	if binding.session == "" || len(binding.session) > 80 || strings.ContainsAny(binding.session, " \t\n/") || !binding.path.IsValid() || !strings.HasPrefix(string(binding.path), "/org/freedesktop/login1/session/") {
		return loginLockBinding{}, errLoginCaptureUnavailable
	}
	// One current binding per reader; retired compositors cannot accumulate state.
	r.lockBindings = map[string]loginLockBinding{identity: binding}
	return binding, nil
}

func (r *loginSeatReader) lock(parent context.Context, session string) error {
	ctx, cancel := context.WithTimeout(parent, time.Second)
	defer cancel()
	return r.bus.Object("org.freedesktop.login1", "/org/freedesktop/login1").CallWithContext(ctx, "org.freedesktop.login1.Manager.LockSession", 0, session).Err
}

// Qualification callers use a temporary observer; the daemon retains its own
// observer for the entire attachment instead of launching loginctl per event.
func loginSeat(ctx context.Context, seat string) (loginSeatState, error) {
	r, err := openLoginSeatReader()
	if err != nil {
		return loginSeatState{}, err
	}
	defer r.close()
	return r.seat(ctx, seat)
}
