//go:build linux

package hostdesktop

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"golang.org/x/sys/unix"
)

// The baseline decoder exists only in this opt-in comparison. Production never
// attaches to an old service; its protocol mismatch requires an explicit update.
func latencyConnection(ctx context.Context, socket string, legacy bool) (*LoginScreenConnection, error) {
	if !legacy {
		return OpenLoginScreenSession(ctx, socket)
	}
	ticketConn, reader, err := loginDial(ctx, socket)
	if err != nil {
		return nil, err
	}
	if err = loginWritePacket(ticketConn, loginServiceHello{Operation: "ticket"}); err != nil {
		ticketConn.Close()
		return nil, err
	}
	var ticket loginServiceReply
	err = loginReadPacket(reader, &ticket)
	ticketConn.Close()
	if err != nil {
		return nil, err
	}
	conn, reader, err := loginDial(ctx, socket)
	if err != nil {
		return nil, err
	}
	if err = loginWritePacket(conn, loginServiceHello{Operation: "attach", Token: ticket.Token}); err != nil {
		conn.Close()
		return nil, err
	}
	var reply loginServiceReply
	if err = loginReadPacket(reader, &reply); err != nil {
		conn.Close()
		return nil, err
	}
	_ = conn.SetDeadline(time.Time{})
	c := &LoginScreenConnection{socket: conn, control: make(chan HostDesktopMessage, 32), media: make(chan HostDesktopMessage, 4), done: make(chan struct{})}
	go func() {
		defer c.Close()
		for {
			message, err := ReadHostDesktopMessage(reader)
			if err != nil {
				return
			}
			destination := c.control
			if message.Type == "frame" || message.Type == "cursor" {
				destination = c.media
			}
			select {
			case destination <- message:
			case <-c.done:
				return
			}
		}
	}()
	return c, nil
}

func latencyCursor(conn *net.UnixConn, command byte) ([]byte, error) {
	_ = conn.SetDeadline(time.Now().Add(time.Second))
	if _, err := conn.Write([]byte{command}); err != nil {
		return nil, err
	}
	packet, control := make([]byte, 80), make([]byte, unix.CmsgSpace(32))
	n, oob, flags, _, err := conn.ReadMsgUnix(packet, control)
	messages, parseErr := unix.ParseSocketControlMessage(control[:oob])
	for _, message := range messages {
		fds, _ := unix.ParseUnixRights(&message)
		for _, fd := range fds {
			_ = unix.Close(fd)
		}
	}
	if err != nil || parseErr != nil || flags&(unix.MSG_TRUNC|unix.MSG_CTRUNC) != 0 {
		return nil, errLoginCaptureUnavailable
	}
	return packet[:n], nil
}

func TestLoginLatencyComparison(t *testing.T) {
	baseline, candidate, worker, mediaRoot := os.Getenv("FLOE_LATENCY_BASELINE"), os.Getenv("FLOE_LATENCY_CANDIDATE"), os.Getenv("FLOE_LATENCY_WORKER"), os.Getenv("FLOE_LOGIN_MEDIA_ROOT")
	if baseline == "" {
		t.Skip("native latency comparison not requested")
	}
	if os.Geteuid() != 0 {
		t.Fatal("administrator authorization required")
	}
	binary, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(binary)
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(data)
	for _, version := range []struct {
		name, service string
		legacy        bool
	}{{"baseline", baseline, true}, {"candidate", candidate, false}} {
		if only := os.Getenv("FLOE_LATENCY_ONLY"); only != "" && only != version.name {
			continue
		}
		for round := 1; round <= 3; round++ {
			parent, err := os.MkdirTemp("/run", "redeven-cursor-latency-")
			if err != nil {
				t.Fatal(err)
			}
			if os.Chown(parent, 0, 1000) != nil || os.Chmod(parent, 0750) != nil {
				t.Fatal("socket directory")
			}
			socket := filepath.Join(parent, "desktop.sock")
			args := []string{"--socket", socket, "--worker", worker, "--runtime-uid", "1000", "--runtime-gid", "1000", "--runtime-sha256", hex.EncodeToString(digest[:])}
			if !version.legacy {
				args = append(args, "--media-root", mediaRoot)
			}
			service := exec.Command(version.service, args...)
			service.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
			if err := service.Start(); err != nil {
				t.Fatal(err)
			}
			func() {
				defer func() {
					_ = unix.Kill(-service.Process.Pid, unix.SIGKILL)
					_ = service.Wait()
					_ = os.RemoveAll(parent)
				}()
				deadline := time.Now().Add(5 * time.Second)
				for {
					if _, err := os.Stat(socket); err == nil {
						break
					}
					if time.Now().After(deadline) {
						t.Fatal("service socket timeout")
					}
					time.Sleep(10 * time.Millisecond)
				}
				cursorParent, cursorChild, err := loginSocketPair()
				if err != nil {
					t.Fatal(err)
				}
				defer cursorParent.Close()
				defer cursorChild.Close()
				exporter := exec.Command(worker, "export")
				exporter.ExtraFiles = []*os.File{cursorChild}
				if exporter.Start() != nil {
					t.Fatal("feedback observer")
				}
				defer func() { _ = exporter.Process.Kill(); _ = exporter.Wait() }()
				shared, err := cursorParent.File()
				if err != nil {
					t.Fatal(err)
				}
				defer shared.Close()
				client := exec.Command(binary, "-test.run", "^TestLoginLatencyClient$", "-test.v")
				client.Env = append(os.Environ(), "FLOE_LATENCY_SOCKET="+socket, "FLOE_LATENCY_LEGACY="+strconv.FormatBool(version.legacy), "FLOE_LATENCY_SERVICE_PID="+strconv.Itoa(service.Process.Pid))
				client.ExtraFiles = []*os.File{shared}
				client.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: 1000, Gid: 1000, Groups: loginRenderGroups(1000)}}
				output, err := client.CombinedOutput()
				t.Logf("%s round=%d\n%s", version.name, round, output)
				if err != nil {
					t.Fatal("latency comparison client failed")
				}
			}()
		}
	}
}

func latencyCPU(pid int) (uint64, error) {
	data, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
	if err != nil {
		return 0, err
	}
	fields := strings.Fields(string(data[strings.LastIndexByte(string(data), ')')+1:]))
	u, e := strconv.ParseUint(fields[11], 10, 64)
	s, e2 := strconv.ParseUint(fields[12], 10, 64)
	if e != nil || e2 != nil {
		return 0, errLoginCaptureUnavailable
	}
	paths, err := filepath.Glob(fmt.Sprintf("/proc/%d/task/*/children", pid))
	if err != nil {
		return 0, err
	}
	seen := map[int]bool{}
	for _, path := range paths {
		children, err := os.ReadFile(path)
		if err != nil {
			return 0, err
		}
		for _, child := range strings.Fields(string(children)) {
			number, _ := strconv.Atoi(child)
			if seen[number] {
				continue
			}
			seen[number] = true
			cpu, err := latencyCPU(number)
			if err != nil {
				return 0, err
			}
			u += cpu
		}
	}
	return u + s, nil
}

func TestLoginLatencyClient(t *testing.T) {
	socket := os.Getenv("FLOE_LATENCY_SOCKET")
	if socket == "" {
		t.Skip("native latency client not requested")
	}
	ctx, cancel := context.WithTimeout(t.Context(), 40*time.Second)
	defer cancel()
	connection, err := latencyConnection(ctx, socket, os.Getenv("FLOE_LATENCY_LEGACY") == "true")
	if err != nil {
		t.Fatal(err)
	}
	defer connection.Close()
	file := os.NewFile(3, "cursor-observer")
	observer, err := net.FileConn(file)
	_ = file.Close()
	if err != nil {
		t.Fatal(err)
	}
	defer observer.Close()
	cursor := observer.(*net.UnixConn)
	if _, err := latencyCursor(cursor, 1); err != nil {
		t.Fatal(err)
	}
	if connection.Send(HostDesktopCommand{Version: 1, ID: 1, Method: "connect", Mode: "control", Picture: &HostDesktopPicture{Mode: "auto", MaxDimension: 1920, FrameRate: 15, NativePixels: true}}, false) != nil {
		t.Fatal("connect")
	}
	var state HostDesktopMessage
	var id uint64 = 10
	var frames, bytes uint64
	consume := func(message HostDesktopMessage) {
		if message.Type == "error" {
			t.Fatal(message.Code)
		}
		if message.Type == "state" {
			state = message
		}
		if message.Type == "frame" {
			id++
			frames++
			bytes += uint64(len(message.Data))
			if connection.Send(HostDesktopCommand{Version: 1, ID: id, Method: "frame_ack", Generation: message.Generation, FrameID: message.FrameID}, false) != nil {
				t.Fatal("ack")
			}
		}
	}
	deadline := time.NewTimer(2 * time.Second)
	defer deadline.Stop()
	for {
		select {
		case message := <-connection.Control():
			consume(message)
		case message := <-connection.Media():
			consume(message)
		case <-deadline.C:
			goto ready
		case <-connection.Done():
			t.Fatal("attachment stopped")
		case <-ctx.Done():
			t.Fatal("capture timeout")
		}
	}
ready:
	if state.State != "active" || frames == 0 {
		t.Fatal("comparison requires an active physical desktop")
	}
	pid, _ := strconv.Atoi(os.Getenv("FLOE_LATENCY_SERVICE_PID"))
	before, err := latencyCPU(pid)
	if err != nil {
		t.Fatal(err)
	}
	staticFrames, staticBytes, start := frames, bytes, time.Now()
	deadline.Reset(5 * time.Second)
	for {
		select {
		case message := <-connection.Control():
			consume(message)
		case message := <-connection.Media():
			consume(message)
		case <-deadline.C:
			goto sampled
		case <-connection.Done():
			t.Fatal("static attachment stopped")
		case <-ctx.Done():
			t.Fatal("static timeout")
		}
	}
sampled:
	after, err := latencyCPU(pid)
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("static cpu_ticks=%d duration_ms=%.1f frames=%d encoded_bytes=%d", after-before, float64(time.Since(start).Microseconds())/1000, frames-staticFrames, bytes-staticBytes)
	var receipts, feedback []float64
	for move := 0; move < 100; move++ {
		x := .25
		if move%2 != 0 {
			x = .75
		}
		id++
		requestID := id
		started := time.Now()
		if connection.Send(HostDesktopCommand{Version: 1, ID: requestID, Method: "input", Generation: state.Generation, Input: &HostDesktopInput{Kind: "move", X: x, Y: .5}}, false) != nil {
			t.Fatal("move")
		}
		received, matched := false, false
		for !received || !matched {
			select {
			case message := <-connection.Control():
				consume(message)
				if message.ID == requestID {
					received = true
					receipts = append(receipts, float64(time.Since(started).Microseconds())/1000)
				}
			case message := <-connection.Media():
				consume(message)
			case <-connection.Done():
				t.Fatal("motion attachment stopped")
			case <-ctx.Done():
				t.Fatal("motion timeout")
			default:
			}
			if !matched {
				packet, err := latencyCursor(cursor, 3)
				if err != nil || len(packet) != 40 {
					t.Fatal("cursor observation failed")
				}
				position := int32(binary.LittleEndian.Uint32(packet[8:]))
				if binary.LittleEndian.Uint32(packet[4:]) == 1 && ((x < .5 && position > 300 && position < 650) || (x > .5 && position > 1250 && position < 1650)) {
					matched = true
					feedback = append(feedback, float64(time.Since(started).Microseconds())/1000)
				}
			}
			if !received || !matched {
				time.Sleep(time.Millisecond)
			}
		}
	}
	for _, item := range []struct {
		name   string
		values []float64
	}{{"input_receipt", receipts}, {"matched_drm_feedback", feedback}} {
		slices.Sort(item.values)
		t.Logf("%s samples=%d median_ms=%.3f p95_ms=%.3f max_ms=%.3f", item.name, len(item.values), item.values[len(item.values)/2], item.values[len(item.values)*95/100], item.values[len(item.values)-1])
	}
}
