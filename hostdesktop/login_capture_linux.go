//go:build linux

package hostdesktop

import (
	"context"
	"encoding/binary"
	"errors"
	"image"
	"io"
	"net"
	"os"
	"os/exec"
	"sync"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

var errLoginConverterStart = errors.New("GPU converter start unavailable")
var errLoginExporterStart = errors.New("DRM exporter start unavailable")

var errLoginCaptureUnavailable = errors.New("DRM scanout capture unavailable")
var errLoginDisplayInactive = errors.New("display_inactive")
var errLoginGPUUnsupported = errors.New("gpu_scanout_unsupported")
var errLoginDRMPermission = errors.New("drm_permission_unavailable")

func loginCaptureStatus(status int32) error {
	switch status {
	case -int32(unix.ENODEV):
		return errors.Join(errLoginCaptureUnavailable, errLoginDisplayInactive)
	case -int32(unix.ENOTSUP), -int32(unix.ENOSYS):
		return errors.Join(errLoginCaptureUnavailable, errLoginGPUUnsupported)
	case -int32(unix.EACCES), -int32(unix.EPERM):
		return errors.Join(errLoginCaptureUnavailable, errLoginDRMPermission)
	default:
		return errLoginCaptureUnavailable
	}
}

// loginDRMCapture isolates device export from GPU-driver conversion. The root
// worker exports read-only DMA-BUF descriptors; only the worker running as the
// Runtime user invokes the host's EGL/GLES driver. Private descriptors and
// bounded pixel replies are the entire boundary between them.
type loginDRMCapture struct {
	mu                  sync.Mutex
	exporter, converter *net.UnixConn
	pixels              *os.File
	cancel              context.CancelFunc
	children            []*exec.Cmd
	closed              bool
}

func loginSocketPair() (*net.UnixConn, *os.File, error) {
	fds, err := unix.Socketpair(unix.AF_UNIX, unix.SOCK_SEQPACKET|unix.SOCK_CLOEXEC, 0)
	if err != nil {
		return nil, nil, err
	}
	parent := os.NewFile(uintptr(fds[0]), "drm-parent")
	child := os.NewFile(uintptr(fds[1]), "drm-child")
	connection, err := net.FileConn(parent)
	_ = parent.Close()
	if err != nil {
		_ = child.Close()
		return nil, nil, err
	}
	socket, ok := connection.(*net.UnixConn)
	if !ok {
		_ = connection.Close()
		_ = child.Close()
		return nil, nil, errLoginCaptureUnavailable
	}
	return socket, child, nil
}

func openLoginDRMCapture(ctx context.Context, worker string, uid, gid uint32, groups []uint32) (*loginDRMCapture, error) {
	if uid == 0 {
		return nil, errors.New("privileged GPU conversion rejected")
	}
	if err := loginRootOwnedExecutable(worker); err != nil {
		return nil, err
	}
	owned, cancel := context.WithCancel(ctx)
	capture := &loginDRMCapture{cancel: cancel}
	success := false
	defer func() {
		if !success {
			_ = capture.close()
		}
	}()
	for _, mode := range []string{"export", "convert"} {
		parent, child, err := loginSocketPair()
		if err != nil {
			return nil, err
		}
		command := exec.CommandContext(owned, worker, mode)
		command.Env = []string{"PATH=/usr/bin:/bin", "LANG=C", "HOME=/nonexistent"}
		command.ExtraFiles = []*os.File{child}
		if mode == "convert" {
			command.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: uid, Gid: gid, Groups: groups}}
			read, write, err := os.Pipe()
			if err != nil {
				_ = parent.Close()
				_ = child.Close()
				return nil, err
			}
			capture.pixels = read
			command.Stdout = write
			if err = command.Start(); err != nil {
				err = errors.Join(errLoginConverterStart, err)
				_ = write.Close()
				_ = parent.Close()
				_ = child.Close()
				return nil, err
			}
			_ = write.Close()
			capture.converter = parent
		} else {
			if err = command.Start(); err != nil {
				err = errors.Join(errLoginExporterStart, err)
				_ = parent.Close()
				_ = child.Close()
				return nil, err
			}
			capture.exporter = parent
		}
		_ = child.Close()
		capture.children = append(capture.children, command)
	}
	success = true
	return capture, nil
}

func (c *loginDRMCapture) frame() (image.Image, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return nil, io.ErrClosedPipe
	}
	deadline := time.Now().Add(3 * time.Second)
	_ = c.exporter.SetDeadline(deadline)
	if _, err := c.exporter.Write([]byte{1}); err != nil {
		return nil, errLoginCaptureUnavailable
	}
	packet := make([]byte, 80)
	control := make([]byte, unix.CmsgSpace(8*4))
	n, oob, flags, _, err := c.exporter.ReadMsgUnix(packet, control)
	if err != nil {
		return nil, errLoginCaptureUnavailable
	}
	messages, parseErr := unix.ParseSocketControlMessage(control[:oob])
	var fds []int
	for _, m := range messages {
		rights, e := unix.ParseUnixRights(&m)
		if e == nil {
			fds = append(fds, rights...)
		}
	}
	defer func() {
		for _, fd := range fds {
			_ = unix.Close(fd)
		}
	}()
	if n != 80 || flags&(unix.MSG_TRUNC|unix.MSG_CTRUNC) != 0 || parseErr != nil || binary.LittleEndian.Uint32(packet[4:8]) != 1 {
		return nil, errLoginCaptureUnavailable
	}
	status := int32(binary.LittleEndian.Uint32(packet[0:4]))
	if status != 0 {
		return nil, loginCaptureStatus(status)
	}
	if len(fds) != 1 {
		return nil, errLoginCaptureUnavailable
	}
	if binary.LittleEndian.Uint32(packet[12:16]) < 2 || binary.LittleEndian.Uint32(packet[16:20]) < 2 {
		return nil, errLoginCaptureUnavailable
	}
	_ = c.converter.SetWriteDeadline(deadline)
	if _, _, err = c.converter.WriteMsgUnix(packet, unix.UnixRights(fds[0]), nil); err != nil {
		return nil, errLoginCaptureUnavailable
	}
	_ = c.pixels.SetReadDeadline(deadline)
	header := make([]byte, 24)
	if _, err = io.ReadFull(c.pixels, header); err != nil {
		return nil, errLoginCaptureUnavailable
	}
	status = int32(binary.LittleEndian.Uint32(header))
	width, height, stride, format, length := int(binary.LittleEndian.Uint32(header[4:])), int(binary.LittleEndian.Uint32(header[8:])), int(binary.LittleEndian.Uint32(header[12:])), binary.LittleEndian.Uint32(header[16:]), int(binary.LittleEndian.Uint32(header[20:]))
	if status != 0 || width < 2 || height < 2 || width > 8192 || height > 8192 || stride < width*4 || length != stride*height || length > hostDesktopPayloadLimit {
		return nil, errLoginCaptureUnavailable
	}
	// FourCC XR24/AR24 are BGRA in memory; XB24/AB24 are RGBA.
	bgra := format == 0x34325258 || format == 0x34325241
	if !bgra && format != 0x34324258 && format != 0x34324241 {
		return nil, errLoginCaptureUnavailable
	}
	data := make([]byte, length)
	if _, err = io.ReadFull(c.pixels, data); err != nil {
		return nil, errLoginCaptureUnavailable
	}
	pixels := image.NewNRGBA(image.Rect(0, 0, width, height))
	for y := 0; y < height; y++ {
		row := data[y*stride : y*stride+width*4]
		dest := pixels.Pix[y*pixels.Stride : y*pixels.Stride+width*4]
		copy(dest, row)
		for x := 0; x < width; x++ {
			i := x * 4
			if bgra {
				dest[i], dest[i+2] = dest[i+2], dest[i]
			}
			dest[i+3] = 255
		}
	}
	return pixels, nil
}

func (c *loginDRMCapture) close() error {
	if c == nil {
		return nil
	}
	// Cancellation precedes the mutex so a stalled GPU operation cannot prevent
	// teardown. Closing the pipes and sockets unblocks any in-flight frame read.
	c.cancel()
	if c.exporter != nil {
		_ = c.exporter.Close()
	}
	if c.converter != nil {
		_ = c.converter.Close()
	}
	if c.pixels != nil {
		_ = c.pixels.Close()
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return nil
	}
	c.closed = true
	for _, child := range c.children {
		_ = child.Wait()
	}
	return nil
}
