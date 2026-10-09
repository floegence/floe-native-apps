//go:build linux

package hostdesktop

import (
	"errors"
	"net"
	"os"

	"golang.org/x/sys/unix"
)

var (
	errLoginConverterStart     = errors.New("GPU converter start unavailable")
	errLoginExporterStart      = errors.New("DRM exporter start unavailable")
	errLoginCaptureUnavailable = errors.New("DRM scanout capture unavailable")
	errLoginDisplayInactive    = errors.New("display_inactive")
)

func loginSocketPair() (*net.UnixConn, *os.File, error) {
	fds, err := unix.Socketpair(unix.AF_UNIX, unix.SOCK_SEQPACKET|unix.SOCK_CLOEXEC, 0)
	if err != nil {
		return nil, nil, err
	}
	parent, child := os.NewFile(uintptr(fds[0]), "drm-parent"), os.NewFile(uintptr(fds[1]), "drm-child")
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
