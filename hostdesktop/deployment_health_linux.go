//go:build linux

package hostdesktop

import (
	"context"
	"errors"
	"net"
	"os"
	"time"

	"golang.org/x/sys/unix"
)

// Deployment checks daemon readiness, not display availability. A disconnected
// monitor must not prevent installation, and cannot assert capture capability.
func loginServiceHealth(ctx context.Context) error {
	deadline := time.NewTimer(5 * time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(25 * time.Millisecond)
	defer ticker.Stop()
	for {
		conn, err := (&net.Dialer{}).DialContext(ctx, "unix", LoginServiceSocket)
		if err == nil {
			socket := conn.(*net.UnixConn)
			var credential *unix.Ucred
			raw, rawErr := socket.SyscallConn()
			var peerErr error
			if rawErr == nil {
				rawErr = raw.Control(func(fd uintptr) {
					credential, peerErr = unix.GetsockoptUcred(int(fd), unix.SOL_SOCKET, unix.SO_PEERCRED)
				})
			}
			_ = socket.Close()
			info, statErr := os.Lstat(LoginServiceSocket)
			if rawErr == nil && peerErr == nil && credential != nil && credential.Uid == 0 && statErr == nil && info.Mode()&os.ModeSocket != 0 && info.Mode().Perm() == 0660 {
				return nil
			}
			return errLoginDeployment
		}
		if !errors.Is(err, os.ErrNotExist) {
			return errLoginDeployment
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-deadline.C:
			return errLoginDeployment
		case <-ticker.C:
		}
	}
}
