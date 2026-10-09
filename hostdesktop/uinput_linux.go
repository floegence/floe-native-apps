//go:build linux

package hostdesktop

import (
	"encoding/binary"
	"errors"
	"io"
	"os"
	"sync"
	"unsafe"

	"golang.org/x/sys/unix"
)

const (
	loginEVSyn          = 0
	loginEVKey          = 1
	loginEVRel          = 2
	loginEVAbs          = 3
	loginAbsX           = 0
	loginAbsY           = 1
	loginRelHWheel      = 6
	loginRelWheel       = 8
	loginBtnLeft        = 272
	loginBtnRight       = 273
	loginBtnMiddle      = 274
	loginBtnSide        = 275
	loginBtnExtra       = 276
	loginPointerMaximum = 65535
)

// Physical code names map to Linux input codes, not character values. The
// display's keyboard layout remains authoritative, including at the greeter.
var loginKeyCodes = map[string]uint16{
	"Escape": 1, "Digit1": 2, "Digit2": 3, "Digit3": 4, "Digit4": 5,
	"Digit5": 6, "Digit6": 7, "Digit7": 8, "Digit8": 9, "Digit9": 10,
	"Digit0": 11, "Minus": 12, "Equal": 13, "Backspace": 14, "Tab": 15,
	"KeyQ": 16, "KeyW": 17, "KeyE": 18, "KeyR": 19, "KeyT": 20,
	"KeyY": 21, "KeyU": 22, "KeyI": 23, "KeyO": 24, "KeyP": 25,
	"BracketLeft": 26, "BracketRight": 27, "Enter": 28, "ControlLeft": 29,
	"KeyA": 30, "KeyS": 31, "KeyD": 32, "KeyF": 33, "KeyG": 34,
	"KeyH": 35, "KeyJ": 36, "KeyK": 37, "KeyL": 38, "Semicolon": 39,
	"Quote": 40, "Backquote": 41, "ShiftLeft": 42, "Backslash": 43,
	"KeyZ": 44, "KeyX": 45, "KeyC": 46, "KeyV": 47, "KeyB": 48,
	"KeyN": 49, "KeyM": 50, "Comma": 51, "Period": 52, "Slash": 53,
	"ShiftRight": 54, "NumpadMultiply": 55, "AltLeft": 56, "Space": 57,
	"CapsLock": 58, "F1": 59, "F2": 60, "F3": 61, "F4": 62,
	"F5": 63, "F6": 64, "F7": 65, "F8": 66, "F9": 67, "F10": 68,
	"NumLock": 69, "ScrollLock": 70, "Numpad7": 71, "Numpad8": 72,
	"Numpad9": 73, "NumpadSubtract": 74, "Numpad4": 75, "Numpad5": 76,
	"Numpad6": 77, "NumpadAdd": 78, "Numpad1": 79, "Numpad2": 80,
	"Numpad3": 81, "Numpad0": 82, "NumpadDecimal": 83, "IntlBackslash": 86,
	"F11": 87, "F12": 88, "NumpadEnter": 96, "ControlRight": 97,
	"NumpadDivide": 98, "PrintScreen": 99, "AltRight": 100, "Home": 102,
	"ArrowUp": 103, "PageUp": 104, "ArrowLeft": 105, "ArrowRight": 106,
	"End": 107, "ArrowDown": 108, "PageDown": 109, "Insert": 110,
	"Delete": 111, "Pause": 119, "MetaLeft": 125, "MetaRight": 126,
	"ContextMenu": 127, "IntlRo": 89, "KanaMode": 93, "Convert": 92,
	"NonConvert": 94, "IntlYen": 124,
}

var errLoginPhysicalInput = errors.New("physical input rejected")

// Each attachment owns its own devices. Destroying the attachment releases
// every held key/button before destroying either device; devices are not shared
// across authenticated sessions or generations.
type loginUInput struct {
	mu                    sync.Mutex
	keyboard, pointer     io.WriteCloser
	heldKeys, heldButtons map[uint16]bool
	destroy               func(io.WriteCloser)
	closed                bool
}

func loginIoctlInt(fd uintptr, request, value uintptr) error {
	_, _, errno := unix.Syscall(unix.SYS_IOCTL, fd, request, value)
	if errno != 0 {
		return errno
	}
	return nil
}

func loginIoctlBytes(fd uintptr, request uintptr, data []byte) error {
	_, _, errno := unix.Syscall(unix.SYS_IOCTL, fd, request, uintptr(unsafe.Pointer(&data[0])))
	if errno != 0 {
		return errno
	}
	return nil
}

func openLoginInputDevice(pointer bool) (*os.File, error) {
	fd, err := unix.Open("/dev/uinput", unix.O_WRONLY|unix.O_NONBLOCK|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	file := os.NewFile(uintptr(fd), "uinput")
	success := false
	defer func() {
		if !success {
			_ = file.Close()
		}
	}()
	set := func(request uintptr, value uint16) error { return loginIoctlInt(file.Fd(), request, uintptr(value)) }
	if err = set(0x40045564, loginEVKey); err != nil {
		return nil, err
	}
	name := "Redeven remote keyboard"
	if pointer {
		name = "Redeven remote pointer"
		for _, code := range []uint16{loginBtnLeft, loginBtnRight, loginBtnMiddle, loginBtnSide, loginBtnExtra} {
			if err = set(0x40045565, code); err != nil {
				return nil, err
			}
		}
		if err = set(0x40045564, loginEVAbs); err != nil {
			return nil, err
		}
		if err = set(0x40045564, loginEVRel); err != nil {
			return nil, err
		}
		for _, code := range []uint16{loginRelWheel, loginRelHWheel} {
			if err = set(0x40045566, code); err != nil {
				return nil, err
			}
		}
		for _, code := range []uint16{loginAbsX, loginAbsY} {
			if err = set(0x40045567, code); err != nil {
				return nil, err
			}
			abs := make([]byte, 28)
			binary.LittleEndian.PutUint16(abs, code)
			binary.LittleEndian.PutUint32(abs[12:16], loginPointerMaximum)
			if err = loginIoctlBytes(file.Fd(), 0x401c5504, abs); err != nil {
				return nil, err
			}
		}
	} else {
		for _, code := range loginKeyCodes {
			if err = set(0x40045565, code); err != nil {
				return nil, err
			}
		}
	}
	setup := make([]byte, 92)
	binary.LittleEndian.PutUint16(setup[0:2], 3) // BUS_USB
	binary.LittleEndian.PutUint16(setup[2:4], 0x2f6f)
	binary.LittleEndian.PutUint16(setup[4:6], 1)
	binary.LittleEndian.PutUint16(setup[6:8], 1)
	copy(setup[8:88], name)
	if err = loginIoctlBytes(file.Fd(), 0x405c5503, setup); err != nil {
		return nil, err
	}
	if err = loginIoctlInt(file.Fd(), 0x5501, 0); err != nil {
		return nil, err
	}
	success = true
	return file, nil
}

func openLoginUInput() (*loginUInput, error) {
	keyboard, err := openLoginInputDevice(false)
	if err != nil {
		return nil, err
	}
	pointer, err := openLoginInputDevice(true)
	if err != nil {
		_ = loginIoctlInt(keyboard.Fd(), 0x5502, 0)
		_ = keyboard.Close()
		return nil, err
	}
	return &loginUInput{keyboard: keyboard, pointer: pointer, heldKeys: map[uint16]bool{}, heldButtons: map[uint16]bool{}, destroy: func(w io.WriteCloser) {
		if f, ok := w.(*os.File); ok {
			_ = loginIoctlInt(f.Fd(), 0x5502, 0)
		}
	}}, nil
}

func loginInputEvent(w io.Writer, typ, code uint16, value int32) error {
	// Linux amd64 and arm64 input_event use the same 64-bit timeval ABI.
	data := make([]byte, 24)
	binary.LittleEndian.PutUint16(data[16:18], typ)
	binary.LittleEndian.PutUint16(data[18:20], code)
	binary.LittleEndian.PutUint32(data[20:24], uint32(value))
	n, err := w.Write(data)
	if err == nil && n != len(data) {
		return io.ErrShortWrite
	}
	return err
}

func (u *loginUInput) input(value *HostDesktopInput) error { return u.inputPhysical(value, false) }

func (u *loginUInput) held() bool {
	u.mu.Lock()
	defer u.mu.Unlock()
	return len(u.heldKeys) != 0 || len(u.heldButtons) != 0
}

func (u *loginUInput) inputPhysical(value *HostDesktopInput, unlocking bool) error {
	if value == nil || !value.valid() || value.Text != "" {
		return errLoginPhysicalInput
	}
	u.mu.Lock()
	defer u.mu.Unlock()
	if u.closed {
		return io.ErrClosedPipe
	}
	if unlocking {
		if value.Kind == "down" && value.Button != 0 {
			return errLoginPhysicalInput
		}
		if value.Kind == "key" && value.Pressed {
			switch value.Code {
			case "ControlLeft", "ControlRight", "MetaLeft", "MetaRight", "AltLeft", "ContextMenu":
				return errLoginPhysicalInput
			case "Insert":
				if u.heldKeys[42] || u.heldKeys[54] {
					return errLoginPhysicalInput
				}
			}
			if u.heldKeys[29] || u.heldKeys[97] || u.heldKeys[125] || u.heldKeys[126] || u.heldKeys[56] {
				return errLoginPhysicalInput
			}
		}
	}
	switch value.Kind {
	case "key":
		code, ok := loginKeyCodes[value.Code]
		if !ok {
			return errLoginPhysicalInput
		}
		v := int32(0)
		if value.Pressed {
			v = 1
			if u.heldKeys[code] {
				v = 2
			}
		}
		// Record down before writing so partial writes still release it on teardown.
		if value.Pressed {
			u.heldKeys[code] = true
		}
		if err := loginInputEvent(u.keyboard, loginEVKey, code, v); err != nil {
			return err
		}
		if err := loginInputEvent(u.keyboard, loginEVSyn, 0, 0); err != nil {
			return err
		}
		if !value.Pressed {
			delete(u.heldKeys, code)
		}
		return nil
	case "move", "down", "up":
		if err := loginInputEvent(u.pointer, loginEVAbs, loginAbsX, int32(value.X*loginPointerMaximum)); err != nil {
			return err
		}
		if err := loginInputEvent(u.pointer, loginEVAbs, loginAbsY, int32(value.Y*loginPointerMaximum)); err != nil {
			return err
		}
		if value.Kind != "move" {
			code := []uint16{loginBtnLeft, loginBtnMiddle, loginBtnRight, loginBtnSide, loginBtnExtra}[value.Button]
			v := int32(0)
			if value.Kind == "down" {
				v = 1
				u.heldButtons[code] = true
			}
			if err := loginInputEvent(u.pointer, loginEVKey, code, v); err != nil {
				return err
			}
			if err := loginInputEvent(u.pointer, loginEVSyn, 0, 0); err != nil {
				return err
			}
			if value.Kind == "up" {
				delete(u.heldButtons, code)
			}
			return nil
		}
		return loginInputEvent(u.pointer, loginEVSyn, 0, 0)
	case "scroll":
		if err := loginInputEvent(u.pointer, loginEVRel, loginRelHWheel, int32(value.DX)); err != nil {
			return err
		}
		if err := loginInputEvent(u.pointer, loginEVRel, loginRelWheel, int32(-value.DY)); err != nil {
			return err
		}
		return loginInputEvent(u.pointer, loginEVSyn, 0, 0)
	default:
		return errLoginPhysicalInput
	}
}

func (u *loginUInput) releaseLocked() error {
	var err error
	for code := range u.heldKeys {
		err = errors.Join(err, loginInputEvent(u.keyboard, loginEVKey, code, 0))
	}
	err = errors.Join(err, loginInputEvent(u.keyboard, loginEVSyn, 0, 0))
	for code := range u.heldButtons {
		err = errors.Join(err, loginInputEvent(u.pointer, loginEVKey, code, 0))
	}
	err = errors.Join(err, loginInputEvent(u.pointer, loginEVSyn, 0, 0))
	clear(u.heldKeys)
	clear(u.heldButtons)
	return err
}
func (u *loginUInput) release() error {
	u.mu.Lock()
	defer u.mu.Unlock()
	if u.closed {
		return nil
	}
	return u.releaseLocked()
}
func (u *loginUInput) close() error {
	if u == nil {
		return nil
	}
	u.mu.Lock()
	defer u.mu.Unlock()
	if u.closed {
		return nil
	}
	err := u.releaseLocked()
	u.closed = true
	for _, w := range []io.WriteCloser{u.keyboard, u.pointer} {
		if u.destroy != nil {
			u.destroy(w)
		}
		err = errors.Join(err, w.Close())
	}
	return err
}
