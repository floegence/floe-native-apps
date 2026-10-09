//go:build linux

package hostdesktop

import (
	"context"
	"errors"
	"testing"

	"github.com/godbus/dbus/v5"
)

func TestLoginSeatFreshAuthorityWithoutRepeatedProcessScans(t *testing.T) {
	properties := map[string]dbus.Variant{}
	for key, value := range map[string]any{"ActiveSession": []any{"3", dbus.ObjectPath("/org/freedesktop/login1/session/_3")},
		"User":   []any{uint32(1000), dbus.ObjectPath("/org/freedesktop/login1/user/_1000")},
		"Active": true, "VTNr": uint32(2), "Type": "wayland", "Class": "user", "Service": "gdm-password", "LockedHint": false} {
		properties[key] = dbus.MakeVariant(value)
	}
	reads, scans := 0, 0
	r := &loginSeatReader{compositor: map[uint32]string{}, readTerminal: func() ([]byte, error) { return []byte("tty2"), nil },
		readEnvironment: func(string) ([]byte, error) { return []byte("XDG_SESSION_ID=3\x00"), nil },
		readProperties: func(context.Context, dbus.ObjectPath, string) (map[string]dbus.Variant, error) {
			reads++
			return properties, nil
		},
		bindCompositor:  func(context.Context, uint32) (string, error) { scans++; return "123:456", nil },
		checkCompositor: func(uint32, string) error { return nil }}
	for range 10 {
		seat, err := r.seat(t.Context(), "seat0")
		if err != nil || seat.state() != "active" {
			t.Fatal(seat, err)
		}
	}
	if scans != 1 || reads != 20 {
		t.Fatalf("scans=%d fresh property reads=%d", scans, reads)
	}
	properties["LockedHint"] = dbus.MakeVariant(true)
	seat, err := r.seat(t.Context(), "seat0")
	if err != nil || seat.state() != "locked" {
		t.Fatal("lock signal cannot replace fresh authority", err)
	}
	r.checkCompositor = func(uint32, string) error { return errors.New("retired") }
	r.bindCompositor = func(context.Context, uint32) (string, error) { return "", errors.New("ambiguous") }
	if _, err := r.seat(t.Context(), "seat0"); err == nil {
		t.Fatal("retired compositor accepted")
	}
}

func TestLoginSeatRejectsUnavailableDBusAndKernelVT(t *testing.T) {
	r := &loginSeatReader{readProperties: func(context.Context, dbus.ObjectPath, string) (map[string]dbus.Variant, error) {
		return nil, errors.New("disconnected")
	}}
	if _, err := r.seat(t.Context(), "seat0"); err == nil {
		t.Fatal("D-Bus failure accepted")
	}
	if _, err := r.seat(t.Context(), "other"); err == nil {
		t.Fatal("unknown seat accepted")
	}
}

func TestLoginSeatUsesBoundGNOMELockerWhenUserManagerSelectedAnotherSession(t *testing.T) {
	physical := map[string]dbus.Variant{}
	for name, value := range map[string]any{
		"ActiveSession": []any{"3", dbus.ObjectPath("/org/freedesktop/login1/session/_3")},
		"User":          []any{uint32(1000), dbus.ObjectPath("/org/freedesktop/login1/user/_1000")},
		"Active":        true, "VTNr": uint32(2), "Type": "x11", "Class": "user", "Service": "gdm-autologin", "LockedHint": false,
	} {
		physical[name] = dbus.MakeVariant(value)
	}
	locker := map[string]dbus.Variant{
		"User": physical["User"], "Active": dbus.MakeVariant(true), "Class": dbus.MakeVariant("user"), "LockedHint": dbus.MakeVariant(true),
	}
	r := &loginSeatReader{
		compositor:      map[uint32]string{},
		readTerminal:    func() ([]byte, error) { return []byte("tty2"), nil },
		readEnvironment: func(string) ([]byte, error) { return []byte("XDG_SESSION_TYPE=x11\x00"), nil },
		bindCompositor:  func(context.Context, uint32) (string, error) { return "123:456", nil },
		checkCompositor: func(uint32, string) error { return nil },
		readProperties: func(_ context.Context, path dbus.ObjectPath, iface string) (map[string]dbus.Variant, error) {
			if iface == "org.freedesktop.login1.User" {
				return map[string]dbus.Variant{"Display": dbus.MakeVariant([]any{"c3", dbus.ObjectPath("/org/freedesktop/login1/session/c3")})}, nil
			}
			if path == "/org/freedesktop/login1/session/c3" {
				return locker, nil
			}
			return physical, nil
		},
	}
	seat, err := r.seat(t.Context(), "seat0")
	if err != nil || seat.state() != "locked" || seat.session != "3" || seat.lockSession != "c3" {
		t.Fatal("physical seat alone bypassed GNOME lock authority", seat, err)
	}
	locker["LockedHint"] = dbus.MakeVariant(false)
	seat, err = r.seat(t.Context(), "seat0")
	if err != nil || seat.state() != "active" {
		t.Fatal("fresh GNOME unlock did not restore authority", err)
	}
	locker["User"] = dbus.MakeVariant([]any{uint32(1001), dbus.ObjectPath("/org/freedesktop/login1/user/_1001")})
	if _, err := r.seat(t.Context(), "seat0"); err == nil {
		t.Fatal("another user's locker accepted")
	}
	locker["User"] = physical["User"]
	locker["LockedHint"] = dbus.MakeVariant("unknown")
	if _, err := r.seat(t.Context(), "seat0"); err == nil {
		t.Fatal("unknown lock state accepted")
	}
	r.readTerminal = func() ([]byte, error) { return []byte("tty3"), nil }
	if _, err := r.seat(t.Context(), "seat0"); err == nil {
		t.Fatal("locker association bypassed the kernel VT")
	}
}
