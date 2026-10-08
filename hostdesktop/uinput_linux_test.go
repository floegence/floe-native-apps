//go:build linux

package hostdesktop

import (
	"bytes"
	"encoding/binary"
	"errors"
	"io"
	"os"
	"path/filepath"
	"testing"
	"time"
)

type loginInputRecorder struct {
	bytes.Buffer
	closed bool
	fail   bool
}

func (r *loginInputRecorder) Close() error { r.closed = true; return nil }
func (r *loginInputRecorder) Write(p []byte) (int, error) {
	if r.fail {
		return 0, io.ErrClosedPipe
	}
	return r.Buffer.Write(p)
}
func recordedLoginInput(t *testing.T) (*loginUInput, *loginInputRecorder, *loginInputRecorder) {
	t.Helper()
	k, p := &loginInputRecorder{}, &loginInputRecorder{}
	return &loginUInput{keyboard: k, pointer: p, heldKeys: map[uint16]bool{}, heldButtons: map[uint16]bool{}}, k, p
}
func eventsForCode(data []byte, typ, code uint16) []int32 {
	var values []int32
	for i := 0; i+24 <= len(data); i += 24 {
		if binary.LittleEndian.Uint16(data[i+16:]) == typ && binary.LittleEndian.Uint16(data[i+18:]) == code {
			values = append(values, int32(binary.LittleEndian.Uint32(data[i+20:])))
		}
	}
	return values
}
func TestLoginUInputPhysicalCodes(t *testing.T) {
	for name, expected := range map[string]uint16{"KeyQ": 16, "KeyS": 31, "KeyZ": 44, "KeyB": 48, "Digit0": 11, "ShiftRight": 54, "AltRight": 100, "NumpadEnter": 96} {
		u, k, _ := recordedLoginInput(t)
		if err := u.input(&HostDesktopInput{Kind: "key", Code: name, Pressed: true}); err != nil {
			t.Fatal(err)
		}
		if got := eventsForCode(k.Bytes(), loginEVKey, expected); len(got) != 1 || got[0] != 1 {
			t.Fatalf("%s mapping: %v", name, got)
		}
		if err := u.close(); err != nil {
			t.Fatal(err)
		}
		if got := eventsForCode(k.Bytes(), loginEVKey, expected); len(got) != 2 || got[1] != 0 {
			t.Fatalf("%s release: %v", name, got)
		}
	}
}
func TestLoginUInputPointerAndRelease(t *testing.T) {
	u, _, p := recordedLoginInput(t)
	if err := u.input(&HostDesktopInput{Kind: "down", Button: 2, X: 1, Y: 0.5}); err != nil {
		t.Fatal(err)
	}
	if got := eventsForCode(p.Bytes(), loginEVAbs, loginAbsX); len(got) != 1 || got[0] != 65535 {
		t.Fatalf("absolute x: %v", got)
	}
	if got := eventsForCode(p.Bytes(), loginEVAbs, loginAbsY); len(got) != 1 || got[0] != 32767 {
		t.Fatalf("absolute y: %v", got)
	}
	if got := eventsForCode(p.Bytes(), loginEVKey, loginBtnRight); len(got) != 1 || got[0] != 1 {
		t.Fatalf("DOM right button: %v", got)
	}
	if err := u.release(); err != nil {
		t.Fatal(err)
	}
	if got := eventsForCode(p.Bytes(), loginEVKey, loginBtnRight); len(got) != 2 || got[1] != 0 {
		t.Fatalf("button release: %v", got)
	}
	if len(u.heldButtons) != 0 {
		t.Fatal("release retained held input")
	}
}
func TestLoginUInputRejectsText(t *testing.T) {
	for _, value := range []*HostDesktopInput{nil, {Kind: "text", Text: "secret"}, {Kind: "paste", Text: "secret"}, {Kind: "key", Code: "KeyA", Text: "secret"}, {Kind: "key", Code: "not-a-physical-code"}, {Kind: "move", X: -1}} {
		u, k, p := recordedLoginInput(t)
		if !errors.Is(u.input(value), errLoginPhysicalInput) {
			t.Fatal("nonphysical input accepted")
		}
		if k.Len() != 0 || p.Len() != 0 {
			t.Fatal("rejected input reached device")
		}
	}
}
func TestLoginUInputUnlockRejectsPasteAndShortcutEvents(t *testing.T) {
	u, keyboard, pointer := recordedLoginInput(t)
	for _, value := range []*HostDesktopInput{
		{Kind: "key", Code: "ControlLeft", Pressed: true}, {Kind: "key", Code: "MetaRight", Pressed: true},
		{Kind: "key", Code: "AltLeft", Pressed: true}, {Kind: "down", Button: 1}, {Kind: "down", Button: 2},
	} {
		if !errors.Is(u.inputPhysical(value, true), errLoginPhysicalInput) {
			t.Fatal("unlock shortcut reached device")
		}
	}
	if keyboard.Len() != 0 || pointer.Len() != 0 {
		t.Fatal("rejected shortcut wrote input")
	}
	if err := u.inputPhysical(&HostDesktopInput{Kind: "key", Code: "ShiftLeft", Pressed: true}, true); err != nil {
		t.Fatal(err)
	}
	if !errors.Is(u.inputPhysical(&HostDesktopInput{Kind: "key", Code: "Insert", Pressed: true}, true), errLoginPhysicalInput) {
		t.Fatal("physical paste reached device")
	}
	if err := u.inputPhysical(&HostDesktopInput{Kind: "key", Code: "Digit4", Pressed: true}, true); err != nil {
		t.Fatal("password shift rejected")
	}
	if err := u.release(); err != nil {
		t.Fatal(err)
	}
	if err := u.inputPhysical(&HostDesktopInput{Kind: "key", Code: "AltRight", Pressed: true}, true); err != nil {
		t.Fatal("password AltGr rejected")
	}
	if err := u.close(); err != nil {
		t.Fatal(err)
	}
}
func TestLoginUInputCloseAfterWriteFailure(t *testing.T) {
	u, k, p := recordedLoginInput(t)
	k.fail = true
	if err := u.input(&HostDesktopInput{Kind: "key", Code: "ShiftLeft", Pressed: true}); err == nil {
		t.Fatal("missing write failure")
	}
	k.fail = false
	if err := u.close(); err != nil {
		t.Fatal(err)
	}
	if !k.closed || !p.closed {
		t.Fatal("device not closed")
	}
	if got := eventsForCode(k.Bytes(), loginEVKey, 42); len(got) != 1 || got[0] != 0 {
		t.Fatalf("failed down not released: %v", got)
	}
	if err := u.input(&HostDesktopInput{Kind: "key", Code: "KeyA"}); !errors.Is(err, io.ErrClosedPipe) {
		t.Fatal("closed device accepted input")
	}
}

// Opt-in real-host smoke test. It requires administrator authorization from the
// qualification workflow and never injects text or requests desktop consent.
func TestLoginUInputRealDevices(t *testing.T) {
	if os.Getenv("FLOE_LOGIN_INPUT_QUALIFY") != "1" {
		t.Skip("real device qualification not requested")
	}
	u, err := openLoginUInput()
	if err != nil {
		t.Fatal(err)
	}
	defer u.close()
	time.Sleep(1500 * time.Millisecond)
	found := map[string]bool{}
	paths, _ := filepath.Glob("/sys/class/input/event*/device/name")
	for _, path := range paths {
		b, _ := os.ReadFile(path)
		found[string(bytes.TrimSpace(b))] = true
	}
	if !found["Redeven remote keyboard"] || !found["Redeven remote pointer"] {
		t.Fatal("kernel input devices did not register")
	}
	if err := u.input(&HostDesktopInput{Kind: "key", Code: "ShiftLeft", Pressed: true}); err != nil {
		t.Fatal(err)
	}
	time.Sleep(100 * time.Millisecond)
	if err := u.release(); err != nil {
		t.Fatal(err)
	}
	if err := u.close(); err != nil {
		t.Fatal(err)
	}
	time.Sleep(100 * time.Millisecond)
	for _, path := range paths {
		b, _ := os.ReadFile(path)
		if bytes.HasPrefix(b, []byte("Redeven remote ")) {
			t.Fatal("attachment retained a kernel input device")
		}
	}
}
