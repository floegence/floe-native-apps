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
