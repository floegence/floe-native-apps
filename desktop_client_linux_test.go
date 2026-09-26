//go:build linux

package nativeapps

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestDesktopClientNativeHelperReattachment(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Fatal("Python 3 is required for the native helper wire contract")
	}
	directory, err := os.MkdirTemp("", "floe-wire-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(directory) })
	ctx, cancel := context.WithTimeout(t.Context(), 10*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, python, "qualification/desktop_control.py", directory)
	output, err := command.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	var diagnostics bytes.Buffer
	command.Stderr = &diagnostics
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = command.Process.Kill() // Only this test's retained child handle.
		_ = command.Wait()
		if diagnostics.Len() != 0 {
			t.Log(diagnostics.String())
		}
	})
	if line, err := bufio.NewReader(output).ReadString('\n'); err != nil || line != "ready\n" {
		t.Fatalf("helper did not open its control endpoint: %q %v", line, err)
	}
	endpoint := DesktopEndpoint{SocketPath: filepath.Join(directory, "control.sock"), Instance: "wire-fixture", Token: strings.Repeat("a", 64)}
	client, state, err := DialDesktop(ctx, endpoint)
	if err != nil || state.State != "waiting" || client.Connection() != 1 {
		t.Fatalf("attach to waiting helper: %v", err)
	}
	defer client.Close()
	bad := endpoint
	bad.Token = strings.Repeat("b", 64)
	if rejected, _, err := DialDesktop(ctx, bad); err == nil || rejected != nil {
		t.Fatal("bad token acquired attachment")
	}
	// Rejected authentication cannot displace a valid owner.
	id, err := client.Send(ctx, DesktopRequest{Method: "status"})
	if err != nil {
		t.Fatal(err)
	}
	event, err := client.Read(ctx)
	var observed DesktopState
	if err != nil || event.ID != id || json.Unmarshal(event.Result, &observed) != nil || observed.State != "waiting" {
		t.Fatalf("invalid token disturbed existing owner: %v", err)
	}
	id, err = client.Send(ctx, DesktopRequest{Method: "input", Connection: client.Connection(), Window: 1, Generation: 1,
		Operation: json.RawMessage(`{"kind":"key","code":30,"pressed":true}`)})
	if err != nil {
		t.Fatal(err)
	}
	event, err = client.Read(ctx)
	if err != nil || event.ID != id || event.Error != "INPUT_TARGET_UNAVAILABLE" {
		t.Fatalf("input before a decoded frame was admitted: %#v %v", event, err)
	}
	second, _, err := DialDesktop(ctx, endpoint)
	if err != nil || second.Connection() != 2 {
		t.Fatalf("authenticated replacement failed: %v", err)
	}
	defer second.Close()
	_ = client.Close() // A late old-owner close cannot revoke the replacement.
	id, err = second.Send(ctx, DesktopRequest{Method: "status"})
	if err != nil {
		t.Fatal(err)
	}
	event, err = second.Read(ctx)
	if err != nil || event.ID != id || event.Error != "" {
		t.Fatalf("old owner affected replacement: %v", err)
	}
	_ = second.Close()
	third, _, err := DialDesktop(ctx, endpoint)
	if err != nil || third.Connection() != 3 {
		t.Fatalf("helper did not survive detach: %v", err)
	}
	_ = third.Close()
}

func TestDesktopClientRejectsPublicOrLinkedEndpoints(t *testing.T) {
	directory, err := os.MkdirTemp("", "floe-wire-mode-")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(directory)
	path := filepath.Join(directory, "control.sock")
	listener, err := net.Listen("unix", path)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	endpoint := DesktopEndpoint{SocketPath: path, Instance: "fixture", Token: strings.Repeat("a", 64)}
	for _, mode := range []os.FileMode{0604, 0640, 0666} {
		if err := os.Chmod(path, mode); err != nil {
			t.Fatal(err)
		}
		if _, _, err := DialDesktop(t.Context(), endpoint); !errors.Is(err, ErrInvalid) {
			t.Fatalf("public endpoint mode %o accepted: %v", mode, err)
		}
	}
	if err := os.Chmod(path, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(directory, 0755); err != nil {
		t.Fatal(err)
	}
	if _, _, err := DialDesktop(t.Context(), endpoint); !errors.Is(err, ErrInvalid) {
		t.Fatalf("public helper directory accepted: %v", err)
	}
	if err := os.Chmod(directory, 0700); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(directory, "alias.sock")
	if err := os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	endpoint.SocketPath = link
	if _, _, err := DialDesktop(t.Context(), endpoint); !errors.Is(err, ErrInvalid) {
		t.Fatalf("linked helper socket accepted: %v", err)
	}
}
