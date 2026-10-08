//go:build linux

package hostdesktop

import (
	"context"
	"errors"
	"image"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

// RunLoginDisplayPower is a fixed child operation, never a renderer-selected
// command. The daemon changes credentials before starting this copy of itself;
// all inherited capabilities are removed before contacting the user session bus.
// Mutter's PowerSaveMode wakes display output without unlocking or injecting
// input, and -1 explicitly means unsupported and must never be changed.
func RunLoginDisplayPower(ctx context.Context) error {
	if os.Geteuid() == 0 {
		return errLoginPeerRejected
	}
	header := unix.CapUserHeader{Version: unix.LINUX_CAPABILITY_VERSION_3}
	caps := [2]unix.CapUserData{}
	if unix.Capset(&header, &caps[0]) != nil || unix.Capget(&header, &caps[0]) != nil {
		return errLoginPeerRejected
	}
	for _, cap := range caps {
		if cap.Effective != 0 || cap.Permitted != 0 || cap.Inheritable != 0 {
			return errLoginPeerRejected
		}
	}
	if loginRootOwnedExecutable("/usr/bin/busctl") != nil {
		return errLoginPeerRejected
	}
	address := "--address=unix:path=/run/user/" + strconv.Itoa(os.Geteuid()) + "/bus"
	args := []string{address, "--timeout=1", "get-property", "org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig", "org.gnome.Mutter.DisplayConfig", "PowerSaveMode"}
	command := exec.CommandContext(ctx, "/usr/bin/busctl", args...)
	command.Env = []string{"PATH=/usr/bin:/bin", "LANG=C", "HOME=/nonexistent"}
	output, err := command.Output()
	if err != nil {
		return errLoginDisplayInactive
	}
	switch strings.TrimSpace(string(output)) {
	case "i 0":
		return nil
	case "i 1", "i 2", "i 3":
	default:
		return errLoginDisplayInactive
	}
	args[2] = "set-property"
	command = exec.CommandContext(ctx, "/usr/bin/busctl", append(args, "i", "0")...)
	command.Env = []string{"PATH=/usr/bin:/bin", "LANG=C", "HOME=/nonexistent"}
	if command.Run() != nil {
		return errLoginDisplayInactive
	}
	return nil
}

func (s *loginServer) captureFrame(ctx context.Context, capture *loginDRMCapture) (image.Image, error) {
	pixels, err := capture.frame()
	if !errors.Is(err, errLoginDisplayInactive) || loginCaptureReason(err) != "DISPLAY_INACTIVE" {
		return pixels, err
	}
	seat, seatErr := loginSeat(ctx, s.config.Seat)
	if seatErr != nil || seat.uid == 0 {
		return nil, errLoginDisplayInactive
	}
	bus := "/run/user/" + strconv.FormatUint(uint64(seat.uid), 10) + "/bus"
	var stat unix.Stat_t
	if unix.Lstat(bus, &stat) != nil || stat.Uid != seat.uid || stat.Mode&unix.S_IFMT != unix.S_IFSOCK {
		return nil, errLoginDisplayInactive
	}
	executable, executableErr := os.Executable()
	if executableErr != nil || loginRootOwnedExecutable(executable) != nil {
		return nil, errLoginDisplayInactive
	}
	wake, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	command := exec.CommandContext(wake, executable, "display-power")
	command.Env = []string{"PATH=/usr/bin:/bin", "LANG=C", "HOME=/nonexistent"}
	command.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: seat.uid, Gid: stat.Gid}}
	if command.Run() != nil {
		return nil, errLoginDisplayInactive
	}
	// Mode activation is asynchronous. Bound settling within this same capture
	// operation; disconnected hardware is never retried or silently substituted.
	for {
		pixels, err = capture.frame()
		if !errors.Is(err, errLoginDisplayInactive) {
			return pixels, err
		}
		select {
		case <-wake.Done():
			return nil, errLoginDisplayInactive
		case <-time.After(50 * time.Millisecond):
		}
	}
}
