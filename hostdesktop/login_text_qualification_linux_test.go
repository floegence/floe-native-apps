//go:build linux

package hostdesktop

import (
	"bufio"
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestLoginTextQualificationClient(t *testing.T) {
	socket, root, fixture := os.Getenv("FLOE_LOGIN_SERVICE_CLIENT_SOCKET"), os.Getenv("FLOE_LOGIN_MEDIA_ROOT"), os.Getenv("FLOE_LOGIN_TEXT_FIXTURE")
	if socket == "" || fixture == "" {
		t.Skip("native text qualification not requested")
	}
	if os.Geteuid() == 0 {
		t.Fatal("text target and client must be unprivileged")
	}
	ctx, cancel := context.WithTimeout(t.Context(), 25*time.Second)
	defer cancel()
	target := exec.CommandContext(ctx, filepath.Join(root, "floe", "host-desktop", "python3"), fixture, root)
	output, err := target.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	target.Stderr = os.Stderr
	if err = target.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = target.Process.Kill(); _ = target.Wait() }()
	scanner := bufio.NewScanner(output)
	if !scanner.Scan() || scanner.Text() != "TEXT_TARGET_READY" {
		t.Fatal("text target unavailable")
	}
	connection, err := OpenLoginScreenSession(ctx, socket)
	if err != nil {
		t.Fatal(err)
	}
	defer connection.Close()
	var generation, frame, sequence uint64
	request := func(command HostDesktopCommand) {
		sequence++
		command.Version, command.ID = 1, sequence
		if command.Generation == 0 && command.Method != "connect" {
			command.Generation = generation
		}
		if err := connection.Send(command, false); err != nil {
			t.Fatal(err)
		}
		for {
			select {
			case message := <-connection.Media():
				if message.Type == "frame" && message.Generation == generation {
					frame = message.FrameID
					// A separate ID keeps media receipts from impersonating the command result.
					if connection.Send(HostDesktopCommand{Version: 1, ID: 1000000 + frame, Method: "frame_ack", Generation: generation, FrameID: frame}, false) != nil {
						t.Fatal("paint receipt")
					}
				}
			case message := <-connection.Control():
				if message.Type == "error" {
					t.Fatalf("text qualification: %s", message.Code)
				}
				if message.Type == "state" {
					if message.State != "active" {
						t.Fatal("text requires active desktop")
					}
					generation = message.Generation
				}
				if message.ID == command.ID && (message.Type == "result" || message.Type == "state") {
					return
				}
			case <-connection.Done():
				t.Fatal("text attachment retired")
			case <-ctx.Done():
				t.Fatal("text qualification timeout")
			}
		}
	}
	request(HostDesktopCommand{Method: "connect", Mode: "control", Picture: &HostDesktopPicture{Mode: "auto", MaxDimension: 1920, FrameRate: 30}})
	for frame == 0 {
		select {
		case message := <-connection.Media():
			if message.Type == "frame" {
				frame = message.FrameID
				request(HostDesktopCommand{Method: "frame_ack", FrameID: frame})
			}
		case message := <-connection.Control():
			if message.Type == "error" {
				t.Fatal(message.Code)
			}
		case <-ctx.Done():
			t.Fatal("text first frame timeout")
		}
	}
	for _, text := range []string{"ab", "C", "\u4e2d\u6587", "\U0001f600", "$"} {
		started := time.Now()
		request(HostDesktopCommand{Method: "input", Input: &HostDesktopInput{Kind: "paste", Text: text}})
		t.Logf("text transfer receipt=%s", time.Since(started))
	}
	for _, down := range []bool{true, false} {
		request(HostDesktopCommand{Method: "input", Input: &HostDesktopInput{Kind: "key", Code: "Enter", Pressed: down}})
	}
	if !scanner.Scan() || !strings.Contains(scanner.Text(), `"matched": true`) {
		t.Fatal("target document did not match confirmed client text")
	}
	if err = target.Wait(); err != nil {
		t.Fatal("text target failed")
	}
	t.Log("rapid ASCII, Unicode, emoji and following Enter reached the target document")
}
