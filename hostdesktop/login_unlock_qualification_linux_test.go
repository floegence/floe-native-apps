//go:build linux

package hostdesktop

import (
	"bufio"
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// Opt-in only: a credential arrives through an inherited input pipe. It is
// never an argument, environment value, fixture, assertion or diagnostic.
// Every character reaches the real GDM password field as physical key events.
func TestLoginUnlockQualificationClient(t *testing.T) {
	if os.Getenv("FLOE_LOGIN_QUALIFY_UNLOCK") != "1" {
		t.Skip("real credential qualification not requested")
	}
	if os.Geteuid() == 0 {
		t.Fatal("client must be unprivileged")
	}
	password := make([]byte, 0, 256)
	reader := bufio.NewReaderSize(os.Stdin, 512)
	for len(password) < 256 {
		ch, err := reader.ReadByte()
		if err != nil {
			t.Fatal("credential input unavailable")
		}
		if ch == '\n' {
			break
		}
		password = append(password, ch)
	}
	defer clear(password)
	if len(password) == 0 || len(password) == 256 {
		t.Fatal("credential input rejected")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Second)
	defer cancel()
	connection, err := OpenLoginScreenSession(ctx, os.Getenv("FLOE_LOGIN_SERVICE_CLIENT_SOCKET"))
	if err != nil {
		t.Fatal("attachment unavailable")
	}
	defer connection.Close()
	var id uint64
	var state HostDesktopMessage
	var frame HostDesktopMessage
	receive := func() HostDesktopMessage {
		select {
		case message := <-connection.Control():
			if message.Type == "state" {
				state = message
			}
			return message
		case message := <-connection.Media():
			if message.Type == "frame" {
				frame = message
			}
			return message
		case <-connection.Done():
			for {
				select {
				case message := <-connection.Control():
					if message.Type == "error" {
						t.Fatalf("attachment rejected: %s", message.Code)
					}
				default:
					t.Fatal("attachment disconnected without error")
				}
			}
		case <-ctx.Done():
			t.Fatal("lock qualification timed out")
		}
		return HostDesktopMessage{}
	}
	request := func(command HostDesktopCommand, expected string) {
		id++
		command.ID, command.Version = id, 1
		if connection.Send(command, false) != nil {
			t.Fatalf("command transport failed: method=%s", command.Method)
		}
		for {
			message := receive()
			if message.ID != id {
				continue
			}
			if expected != "" {
				if message.Type != "error" || message.Code != expected {
					t.Fatal("input rejection contract failed")
				}
			} else if message.Type != "result" && message.Type != "state" {
				t.Fatalf("command rejected: %s", message.Code)
			}
			return
		}
	}
	waitFrame := func(target string) HostDesktopMessage {
		for state.State != target || frame.FrameID == 0 || frame.Generation != state.Generation {
			message := receive()
			if message.Type == "error" {
				t.Fatalf("capture rejected: %s", message.Code)
			}
		}
		return frame
	}
	paint := func(frame HostDesktopMessage) {
		request(HostDesktopCommand{Method: "frame_ack", Generation: frame.Generation, FrameID: frame.FrameID}, "")
	}
	pause := func(duration time.Duration) {
		timer := time.NewTimer(duration)
		defer timer.Stop()
		for {
			select {
			case message := <-connection.Control():
				if message.Type == "state" {
					state = message
				}
				if message.Type == "error" {
					t.Fatalf("capture rejected: %s", message.Code)
				}
			case message := <-connection.Media():
				if message.Type == "frame" {
					frame = message
				}
			case <-connection.Done():
				t.Fatal("attachment disconnected during observation")
			case <-timer.C:
				return
			case <-ctx.Done():
				t.Fatal("observation timed out")
			}
		}
	}
	request(HostDesktopCommand{Method: "connect", Mode: "control", Picture: &HostDesktopPicture{Mode: "auto", MaxDimension: 1920, FrameRate: 15}}, "")
	for state.State != "active" && state.State != "locked" {
		receive()
	}
	if state.State == "active" {
		active := waitFrame("active")
		paint(active)
		request(HostDesktopCommand{Method: "lock", Generation: active.Generation}, "")
		locked := waitFrame("locked")
		if locked.Generation == active.Generation {
			t.Fatal("lock retained generation")
		}
	}
	locked := waitFrame("locked")
	paint(locked)
	request(HostDesktopCommand{Method: "input", Generation: locked.Generation, Input: &HostDesktopInput{Kind: "key", Code: "KeyA", Pressed: true}}, "INPUT_REJECTED")
	t.Log("lock screen captured; ordinary input rejected")
	key := func(code string, pressed bool) {
		request(HostDesktopCommand{Method: "unlock_input", Generation: locked.Generation, FrameID: locked.FrameID, Input: &HostDesktopInput{Kind: "key", Code: code, Pressed: pressed}}, "")
	}
	tap := func(code string) { key(code, true); key(code, false) }
	// A real keyboard can leave Caps Lock enabled before this fixture starts.
	// Observe the kernel LEDs instead of assuming lowercase physical keycodes.
	leds, _ := filepath.Glob("/sys/class/leds/*capslock/brightness")
	caps := false
	for _, path := range leds {
		value, err := os.ReadFile(path)
		if err == nil && strings.TrimSpace(string(value)) == "1" {
			caps = true
		}
	}
	if caps {
		tap("CapsLock")
		pause(100 * time.Millisecond)
		t.Log("observed Caps Lock enabled; normalized using a physical key event")
	}
	// Wake GNOME's screen shield, then submit exactly one invalid credential.
	tap("Enter")
	pause(750 * time.Millisecond)
	tap("KeyX")
	tap("KeyX")
	tap("Enter")
	pause(3 * time.Second)
	if state.State != "locked" {
		t.Fatal("invalid credential changed desktop authority")
	}
	// Observe fresh state rather than inferring success from the input receipt.
	request(HostDesktopCommand{Method: "release_input", Generation: locked.Generation}, "")
	if state.State != "locked" {
		t.Fatal("invalid credential unlocked the desktop")
	}
	t.Log("invalid physical credential remained locked")
	for i := 0; i < 24; i++ {
		tap("Backspace")
	}
	for _, ch := range password {
		code, shift := "", false
		switch {
		case ch >= 'a' && ch <= 'z':
			code = "Key" + string(ch-'a'+'A')
		case ch >= 'A' && ch <= 'Z':
			code, shift = "Key"+string(ch), true
		case ch >= '0' && ch <= '9':
			code = "Digit" + string(ch)
		case ch == '$':
			code, shift = "Digit4", true
		default:
			t.Fatal("qualification keyboard layout unsupported")
		}
		if shift {
			key("ShiftLeft", true)
		}
		tap(code)
		if shift {
			key("ShiftLeft", false)
		}
	}
	clear(password)
	tap("Enter")
	successor := waitFrame("active")
	if successor.Generation == locked.Generation {
		t.Fatal("unlock retained generation")
	}
	request(HostDesktopCommand{Method: "unlock_input", Generation: locked.Generation, FrameID: locked.FrameID, Input: &HostDesktopInput{Kind: "key", Code: "ShiftLeft", Pressed: true}}, "GENERATION_RETIRED")
	request(HostDesktopCommand{Method: "input", Generation: successor.Generation, Input: &HostDesktopInput{Kind: "key", Code: "ShiftLeft", Pressed: true}}, "INPUT_REJECTED")
	paint(successor)
	request(HostDesktopCommand{Method: "input", Generation: successor.Generation, Input: &HostDesktopInput{Kind: "key", Code: "ShiftLeft", Pressed: true}}, "")
	request(HostDesktopCommand{Method: "release_input", Generation: successor.Generation}, "")
	t.Log("physical credential unlocked GDM; retired input and unpainted successor rejected; new paint restored control")
}
