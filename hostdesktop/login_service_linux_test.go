//go:build linux

package hostdesktop

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

// This qualifier exercises the actual Unix socket, peer identity, single-use
// ticket, separate GPU worker, frame receipt and input teardown on a native host.
func TestLoginServiceRealAttachment(t *testing.T) { qualifyLoginService(t, false) }
func TestLoginServiceDisconnectedDisplay(t *testing.T) {
	if os.Getenv("FLOE_LOGIN_EXPECT_DISCONNECTED") != "1" {
		t.Skip("disconnected-display qualification not requested")
	}
	qualifyLoginService(t, true)
}
func qualifyLoginService(t *testing.T, disconnected bool) {
	worker := os.Getenv("FLOE_LOGIN_SERVICE_WORKER")
	if worker == "" {
		t.Skip("native service qualification not requested")
	}
	if os.Geteuid() != 0 {
		t.Fatal("qualification requires administrator authorization")
	}
	binary, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	file, err := os.Open("/proc/self/exe")
	if err != nil {
		t.Fatal(err)
	}
	hash := sha256.New()
	_, err = io.Copy(hash, file)
	_ = file.Close()
	if err != nil {
		t.Fatal(err)
	}
	parent, err := os.MkdirTemp("/run", "redeven-desktop-qualification-")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(parent)
	if err = os.Chown(parent, 0, 1000); err != nil {
		t.Fatal(err)
	}
	if err = os.Chmod(parent, 0750); err != nil {
		t.Fatal(err)
	}
	socket := filepath.Join(parent, "desktop.sock")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	stopped := make(chan error, 1)
	go func() {
		stopped <- RunLoginScreenService(ctx, LoginServiceConfig{SocketPath: socket, WorkerPath: worker, RuntimeUID: 1000, RuntimeGID: 1000, RuntimeSHA256: hex.EncodeToString(hash.Sum(nil))})
	}()
	deadline := time.Now().Add(5 * time.Second)
	for {
		if _, err = os.Stat(socket); err == nil {
			break
		}
		select {
		case err := <-stopped:
			t.Fatalf("service start: %v", err)
		default:
		}
		if time.Now().After(deadline) {
			t.Fatal("service socket not ready")
		}
		time.Sleep(10 * time.Millisecond)
	}
	client := exec.Command(binary, "-test.run", "^TestLoginServiceQualificationClient$", "-test.v")
	client.Env = append(os.Environ(), "FLOE_LOGIN_SERVICE_CLIENT_SOCKET="+socket)
	if disconnected {
		client.Env = append(client.Env, "FLOE_LOGIN_SERVICE_CLIENT_NO_SCANOUT=1")
	}
	client.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: 1000, Gid: 1000, Groups: loginRenderGroups(1000)}}
	output, err := client.CombinedOutput()
	t.Log(string(output))
	if err != nil {
		t.Fatalf("unprivileged client: %v", err)
	}
	cancel()
	select {
	case err := <-stopped:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("service stop retained attachment")
	}
}
func TestLoginServiceQualificationClient(t *testing.T) {
	socket := os.Getenv("FLOE_LOGIN_SERVICE_CLIENT_SOCKET")
	if socket == "" {
		t.Skip("native client qualification not requested")
	}
	if os.Geteuid() == 0 {
		t.Fatal("client must be unprivileged")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	status, err := InstalledLoginServiceStatus(ctx, socket)
	if err != nil || status.State != ServiceActive {
		t.Fatalf("service status: %+v %v", status, err)
	}
	connection, err := OpenLoginScreenSession(ctx, socket)
	if err != nil {
		t.Fatal(err)
	}
	defer connection.Close()
	if os.Getenv("FLOE_LOGIN_SERVICE_CLIENT_NO_SCANOUT") == "1" {
		if err = connection.Send(HostDesktopCommand{Version: 1, ID: 1, Method: "probe"}, false); err != nil {
			t.Fatal(err)
		}
		select {
		case message := <-connection.Control():
			cap := message.Capabilities
			if cap == nil || cap.State != "unavailable" || cap.Reason != "DISPLAY_DISCONNECTED" || cap.Screen || cap.Input || cap.Unlock || cap.LockedScreen {
				t.Fatalf("disconnected probe type=%s code=%s capabilities=%+v", message.Type, message.Code, cap)
			}
			t.Log("disconnected display rejected with DISPLAY_DISCONNECTED; no Portal path")
		case <-ctx.Done():
			t.Fatal("capability probe timed out")
		}
		return
	}
	if err = connection.Send(HostDesktopCommand{Version: 1, ID: 1, Method: "connect", Mode: "control", Picture: &HostDesktopPicture{Mode: "auto", MaxDimension: 1920, FrameRate: 15, NativePixels: true}}, false); err != nil {
		t.Fatal(err)
	}
	var currentState string
	var frame HostDesktopMessage
	for frame.FrameID == 0 {
		select {
		case message := <-connection.Media():
			if message.Type == "frame" {
				frame = message
			}
		case message := <-connection.Control():
			if message.Type == "state" {
				currentState = message.State
			}
			if message.Type == "error" {
				t.Fatalf("service error: %s", message.Code)
			}
		case <-connection.Done():
			t.Fatal("service disconnected before frame")
		case <-ctx.Done():
			t.Fatal("service frame timeout")
		}
	}
	t.Logf("service frame %dx%d, generation %d", frame.Width, frame.Height, frame.Generation)
	if err = connection.Send(HostDesktopCommand{Version: 1, ID: 2, Method: "frame_ack", Generation: frame.Generation, FrameID: frame.FrameID}, false); err != nil {
		t.Fatal(err)
	}
	// Prove generation rejection before testing teardown. The service must return
	// a fixed error code and cannot pass a stale event to the input device.
	if err = connection.Send(HostDesktopCommand{Version: 1, ID: 3, Method: "input", Generation: frame.Generation - 1, Input: &HostDesktopInput{Kind: "key", Code: "ShiftLeft", Pressed: true}}, false); err != nil {
		t.Fatal(err)
	}
	seenAck, seenRejected := false, false
	for !seenAck || !seenRejected {
		select {
		case message := <-connection.Control():
			if message.Type == "state" {
				currentState = message.State
			}
			if message.ID == 2 && message.Type == "result" {
				seenAck = true
			}
			if message.ID == 3 && message.Type == "error" && message.Code == "GENERATION_RETIRED" {
				seenRejected = true
			}
			if message.Type == "error" && message.ID != 3 {
				t.Fatalf("service error: %s", message.Code)
			}
		case <-connection.Done():
			t.Fatal("attachment retired during validation")
		case <-ctx.Done():
			t.Fatal("protocol validation timeout")
		}
	}
	method := "input"
	// The original state can be locked on a qualification host; the safe Shift
	// event still requires the painted lock frame and explicit unlock method.
	if currentState != "active" && currentState != "locked" {
		t.Fatal("attachment did not report its state")
	}
	if currentState == "locked" {
		method = "unlock_input"
	}
	held := HostDesktopCommand{Version: 1, ID: 4, Method: method, Generation: frame.Generation, Input: &HostDesktopInput{Kind: "key", Code: "ShiftLeft", Pressed: true}}
	if method == "unlock_input" {
		held.FrameID = frame.FrameID
	}
	if err = connection.Send(held, false); err != nil {
		t.Fatal(err)
	}
	waiting := true
	for waiting {
		select {
		case message := <-connection.Control():
			if message.ID == 4 {
				if message.Type != "result" {
					t.Fatalf("physical input rejected: %s", message.Code)
				}
				waiting = false
			}
		case <-ctx.Done():
			t.Fatal("input receipt timeout")
		}
	}
	if os.Getenv("FLOE_LOGIN_HOLD_INPUT") == "1" {
		fmt.Println("qualification_input_held")
		for {
			select {
			case <-connection.Media():
			case message := <-connection.Control():
				if message.Type == "state" && message.Generation != frame.Generation {
					return
				}
			case <-connection.Done():
				return
			case <-ctx.Done():
				t.Fatal("held attachment was not retired")
			}
		}
	}
	_ = connection.Close()
	// Kernel devices must disappear after connection teardown. Poll only the
	// task-owned names; unrelated host keyboards and users are never touched.
	deadline := time.Now().Add(3 * time.Second)
	for {
		paths, _ := filepath.Glob("/sys/class/input/event*/device/name")
		remaining := false
		for _, path := range paths {
			data, _ := os.ReadFile(path)
			if string(data) == "Redeven remote keyboard\n" || string(data) == "Redeven remote pointer\n" {
				remaining = true
			}
		}
		if !remaining {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("disconnect retained virtual input")
		}
		time.Sleep(20 * time.Millisecond)
	}
}
