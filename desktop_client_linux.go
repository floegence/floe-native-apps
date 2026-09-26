//go:build linux

package nativeapps

import (
	"context"
	"net"
	"os"
	"path/filepath"
	"strings"
	"syscall"

	"golang.org/x/sys/unix"
)

// DialDesktop authenticates to an already running Linux helper and returns its
// initial observed state. It neither prepares nor launches an application. The
// endpoint is a 0600 Unix socket in an owner-only 0700 directory; kernel peer
// credentials must match this user before any token is sent. Close detaches only
// this viewer. The caller owns authorization, lifetime context, and recovery UI.
func DialDesktop(ctx context.Context, endpoint DesktopEndpoint) (*DesktopConnection, DesktopState, error) {
	if !filepath.IsAbs(endpoint.SocketPath) || len(endpoint.Instance) == 0 || len(endpoint.Instance) > 128 ||
		len(endpoint.Token) != 64 || strings.Trim(endpoint.Token, "0123456789abcdef") != "" {
		return nil, DesktopState{}, ErrInvalid
	}
	for path, mode := range map[string]os.FileMode{
		filepath.Dir(endpoint.SocketPath): os.ModeDir | 0700,
		endpoint.SocketPath:               os.ModeSocket | 0600,
	} {
		info, err := os.Lstat(path)
		if err != nil {
			return nil, DesktopState{}, err
		}
		stat, ok := info.Sys().(*syscall.Stat_t)
		if !ok || int(stat.Uid) != os.Getuid() || info.Mode() != mode {
			return nil, DesktopState{}, ErrInvalid
		}
	}
	conn, err := (&net.Dialer{}).DialContext(ctx, "unix", endpoint.SocketPath)
	if err != nil {
		return nil, DesktopState{}, err
	}
	raw, err := conn.(*net.UnixConn).SyscallConn()
	var credentials *unix.Ucred
	if err == nil {
		var controlErr error
		controlErr = raw.Control(func(fd uintptr) {
			credentials, err = unix.GetsockoptUcred(int(fd), unix.SOL_SOCKET, unix.SO_PEERCRED)
		})
		if controlErr != nil {
			err = controlErr
		}
	}
	if err != nil || credentials == nil || int(credentials.Uid) != os.Getuid() {
		_ = conn.Close()
		return nil, DesktopState{}, ErrInvalid
	}
	return authenticateDesktop(ctx, conn, endpoint)
}
