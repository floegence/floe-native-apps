//go:build linux

package hostdesktop

import (
	"bufio"
	"context"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestLoginTextQualificationClient(t *testing.T) {
	for _, mode := range []string{"text", "readonly", "cancel"} {
		t.Run(mode, func(t *testing.T) { qualifyLoginText(t, mode) })
	}
}

func qualifyLoginText(t *testing.T, mode string) {
	socket, root, fixture := os.Getenv("FLOE_LOGIN_SERVICE_CLIENT_SOCKET"), os.Getenv("FLOE_LOGIN_MEDIA_ROOT"), os.Getenv("FLOE_LOGIN_TEXT_FIXTURE")
	if socket == "" || fixture == "" {
		t.Skip("native text qualification not requested")
	}
	if os.Geteuid() == 0 {
		t.Fatal("text target and client must be unprivileged")
	}
	ctx, cancel := context.WithTimeout(t.Context(), 25*time.Second)
	defer cancel()
	python := filepath.Join(root, "floe", "host-desktop", "python3")
	if os.Getenv("FLOE_LOGIN_QUALIFY_SYSTEM_TEXT") == "1" {
		python = "/usr/bin/python3"
	}
	target := exec.CommandContext(ctx, python, fixture, root)
	target.Env = append(os.Environ(), "FLOE_LOGIN_TEXT_CASE="+mode)
	if os.Getenv("FLOE_LOGIN_QUALIFY_SYSTEM_TEXT") == "1" {
		account, err := user.LookupId(strconv.Itoa(os.Getuid()))
		if err != nil {
			t.Fatal(err)
		}
		target.Env = append(target.Env, "HOME="+account.HomeDir,
			"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/"+strconv.Itoa(os.Getuid())+"/bus")
	}
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
					if message.Code == "GENERATION_RETIRED" && message.ID >= 1000000 {
						// Cancellation retires media receipts queued for the old paint.
						continue
					}
					t.Fatalf("text qualification: %s", message.Code)
				}
				if message.Type == "state" {
					if message.State != "active" {
						t.Fatal("text requires active desktop")
					}
					if generation != message.Generation {
						frame = 0
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
	paint := func() {
		for frame == 0 {
			select {
			case message := <-connection.Media():
				if message.Type == "frame" && message.Generation == generation {
					frame = message.FrameID
					request(HostDesktopCommand{Method: "frame_ack", FrameID: frame})
				}
			case message := <-connection.Control():
				if message.Type == "error" {
					if message.Code == "GENERATION_RETIRED" && message.ID >= 1000000 {
						continue
					}
					t.Fatal(message.Code)
				}
			case <-ctx.Done():
				t.Fatal("text first frame timeout")
			}
		}
	}
	paint()
	if mode != "text" {
		command := HostDesktopCommand{Method: "input", Input: &HostDesktopInput{Kind: "paste", Text: "bounded qualifier"}}
		if mode == "readonly" {
			request(command)
		} else {
			sequence++
			command.Version, command.ID, command.Generation = 1, sequence, generation
			if err := connection.Send(command, false); err != nil {
				t.Fatal(err)
			}
		}
		if !scanner.Scan() || scanner.Text() != "PASTE_CHORD_READY" {
			t.Fatal("target did not observe the paste chord")
		}
		if mode == "cancel" {
			request(HostDesktopCommand{Method: "release_input"})
			paint()
		}
		for _, down := range []bool{true, false} {
			request(HostDesktopCommand{Method: "input", Input: &HostDesktopInput{Kind: "key", Code: "KeyA", Pressed: down}})
		}
	} else {
		for _, text := range []string{"ab", "C", "\u4e2d\u6587", "\U0001f600", "$"} {
			started := time.Now()
			request(HostDesktopCommand{Method: "input", Input: &HostDesktopInput{Kind: "paste", Text: text}})
			t.Logf("text transfer receipt=%s", time.Since(started))
		}
		for _, down := range []bool{true, false} {
			request(HostDesktopCommand{Method: "input", Input: &HostDesktopInput{Kind: "key", Code: "Enter", Pressed: down}})
		}
	}
	if !scanner.Scan() || !strings.Contains(scanner.Text(), `"matched": true`) {
		t.Fatal("target document did not match confirmed client text")
	}
	if err = target.Wait(); err != nil {
		t.Fatal("text target failed")
	}
	t.Log("target verified document input or modifier release after readonly transfer/cancellation")
}
