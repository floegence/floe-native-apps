//go:build linux

package hostdesktop

import (
	"context"
	"errors"
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
	readProperties  func(context.Context, dbus.ObjectPath, string) (map[string]dbus.Variant, error)
	readTerminal    func() ([]byte, error)
	bindCompositor  func(context.Context, uint32) (string, error)
	checkCompositor func(uint32, string) error
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
	return loginSeatState{session: id, kind: text("Class"), compositor: identity, uid: uid, vt: vt, locked: locked}, nil
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
