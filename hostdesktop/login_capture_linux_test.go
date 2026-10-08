//go:build linux

package hostdesktop

import (
	"context"
	"encoding/binary"
	"errors"
	"image/png"
	"net"
	"os"
	"testing"
	"time"

	"golang.org/x/sys/unix"
)

func TestLoginDRMConverterUnsupportedPreservesCapabilityReason(t *testing.T) {
	exporter, exportChild, err := loginSocketPair()
	if err != nil {
		t.Fatal(err)
	}
	defer exporter.Close()
	exportPeer, err := net.FileConn(exportChild)
	_ = exportChild.Close()
	if err != nil {
		t.Fatal(err)
	}
	defer exportPeer.Close()
	converter, convertChild, err := loginSocketPair()
	if err != nil {
		t.Fatal(err)
	}
	defer converter.Close()
	convertPeer, err := net.FileConn(convertChild)
	_ = convertChild.Close()
	if err != nil {
		t.Fatal(err)
	}
	defer convertPeer.Close()
	read, write, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer read.Close()
	defer write.Close()
	frameFD, err := os.Open("/dev/null")
	if err != nil {
		t.Fatal(err)
	}
	defer frameFD.Close()
	descriptor := int(frameFD.Fd())
	go func() {
		command := make([]byte, 1)
		if _, err := exportPeer.Read(command); err != nil {
			return
		}
		packet := make([]byte, 80)
		binary.LittleEndian.PutUint32(packet[4:], 1)
		binary.LittleEndian.PutUint32(packet[12:], 2)
		binary.LittleEndian.PutUint32(packet[16:], 2)
		_, _, _ = exportPeer.(*net.UnixConn).WriteMsgUnix(packet, unix.UnixRights(descriptor), nil)
	}()
	go func() {
		packet, rights := make([]byte, 80), make([]byte, unix.CmsgSpace(32))
		_, n, _, _, err := convertPeer.(*net.UnixConn).ReadMsgUnix(packet, rights)
		if err != nil {
			return
		}
		messages, _ := unix.ParseSocketControlMessage(rights[:n])
		for _, message := range messages {
			fds, _ := unix.ParseUnixRights(&message)
			for _, fd := range fds {
				_ = unix.Close(fd)
			}
		}
		header := make([]byte, 24)
		status := -int32(unix.ENOTSUP)
		binary.LittleEndian.PutUint32(header, uint32(status))
		_, _ = write.Write(header)
	}()
	capture := &loginDRMCapture{exporter: exporter, converter: converter, pixels: read}
	_, err = capture.frame()
	if !errors.Is(err, errLoginGPUUnsupported) {
		t.Fatalf("GPU format unsupported became a transient capture error: %v", err)
	}
}

func TestLoginDRMSeatChangeReselectsScanoutOnce(t *testing.T) {
	exporter, child, err := loginSocketPair()
	if err != nil {
		t.Fatal(err)
	}
	defer exporter.Close()
	peer, err := net.FileConn(child)
	_ = child.Close()
	if err != nil {
		t.Fatal(err)
	}
	defer peer.Close()
	commands := make(chan byte, 2)
	go func() {
		for i := 0; i < 2; i++ {
			command := make([]byte, 1)
			if _, err := peer.Read(command); err != nil {
				return
			}
			commands <- command[0]
			packet := make([]byte, 80)
			status := -int32(unix.ENODEV)
			binary.LittleEndian.PutUint32(packet, uint32(status))
			binary.LittleEndian.PutUint32(packet[4:], 1)
			_, _ = peer.Write(packet)
		}
	}()
	capture := &loginDRMCapture{exporter: exporter}
	capture.invalidate()
	_, _ = capture.frame()
	_, _ = capture.frame()
	for _, expected := range []byte{2, 1} {
		select {
		case command := <-commands:
			if command != expected {
				t.Fatalf("export operation %d; expected %d", command, expected)
			}
		case <-time.After(time.Second):
			t.Fatal("export operation did not complete")
		}
	}
}

func TestLoginVirtualTerminalRequiresKernelAndLogindAgreement(t *testing.T) {
	for _, test := range []struct {
		vt, active, kernel string
		ready              bool
	}{
		{"1", "yes", "tty1\n", true},
		{"2", "yes", "tty2", true},
		{"1", "yes", "tty2", false},
		{"1", "no", "tty1", false},
		{"0", "yes", "tty0", false},
		{"", "yes", "tty1", false},
		{"1x", "yes", "tty1", false},
		{"1", "yes", "tty1x", false},
	} {
		if got := loginVirtualTerminalReady(test.vt, test.active, test.kernel); got != test.ready {
			t.Fatalf("VT=%q Active=%q kernel=%q: ready=%v", test.vt, test.active, test.kernel, got)
		}
	}
}

func TestLoginDRMRealCapture(t *testing.T) {
	worker := os.Getenv("FLOE_LOGIN_CAPTURE_WORKER")
	if worker == "" {
		t.Skip("real DRM capture qualification not requested")
	}
	if os.Geteuid() != 0 {
		t.Fatal("qualification requires administrator authorization")
	}
	// Wake the display through a task-owned input device, without Portal or any
	// local graphical consent. Shift injects no character or credential.
	input, err := openLoginUInput()
	if err != nil {
		t.Fatal(err)
	}
	defer input.close()
	time.Sleep(1500 * time.Millisecond)
	if err = input.input(&HostDesktopInput{Kind: "key", Code: "ShiftLeft", Pressed: true}); err != nil {
		t.Fatal(err)
	}
	time.Sleep(100 * time.Millisecond)
	if err = input.release(); err != nil {
		t.Fatal(err)
	}
	time.Sleep(1500 * time.Millisecond)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	capture, err := openLoginDRMCapture(ctx, worker, 1000, 1000, loginRenderGroups(1000))
	if err != nil {
		t.Fatal(err)
	}
	defer capture.close()
	frame, err := capture.frame()
	if err != nil {
		t.Fatal(err)
	}
	size := frame.Bounds()
	nonblack := 0
	for y := size.Min.Y; y < size.Max.Y; y++ {
		for x := size.Min.X; x < size.Max.X; x++ {
			r, g, b, _ := frame.At(x, y).RGBA()
			if r|g|b != 0 {
				nonblack++
			}
		}
	}
	if nonblack == 0 {
		t.Fatal("captured frame is entirely black")
	}
	t.Logf("captured %dx%d scanout; %d nonblack pixels", size.Dx(), size.Dy(), nonblack)
	if path := os.Getenv("FLOE_LOGIN_CAPTURE_OUTPUT"); path != "" {
		file, err := os.OpenFile(path, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
		if err != nil {
			t.Fatal(err)
		}
		err = png.Encode(file, frame)
		closeErr := file.Close()
		if err != nil {
			t.Fatal(err)
		}
		if closeErr != nil {
			t.Fatal(closeErr)
		}
	}
}
