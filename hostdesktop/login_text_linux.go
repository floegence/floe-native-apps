//go:build linux

package hostdesktop

import (
	"bufio"
	"context"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

// The attachment is the sole permission owner. This fixed, unprivileged child
// receives admitted client text over an inherited socket, never OS credentials.
type loginTextWorker struct {
	connection *net.UnixConn
	reader     *bufio.Reader
	child      *exec.Cmd
	cancel     context.CancelFunc
}

func openLoginText(ctx context.Context, config LoginServiceConfig, seat loginSeatState) (*loginTextWorker, error) {
	if seat.state() != "active" || seat.uid == 0 || loginCompositorCurrent(seat.uid, seat.compositor) != nil {
		return nil, errLoginAuthority
	}
	var bus unix.Stat_t
	if unix.Lstat(filepath.Join("/run/user", strconv.FormatUint(uint64(seat.uid), 10), "bus"), &bus) != nil || bus.Uid != seat.uid || bus.Mode&unix.S_IFMT != unix.S_IFSOCK {
		return nil, errLoginAuthority
	}
	parent, child, err := loginSocketPair()
	if err != nil {
		return nil, err
	}
	defer child.Close()
	executable := filepath.Join(filepath.Dir(config.WorkerPath), "floe-host-desktop-service")
	if loginRootOwnedExecutable(executable) != nil {
		parent.Close()
		return nil, errLoginPeerRejected
	}
	owned, cancel := context.WithCancel(ctx)
	command := exec.CommandContext(owned, executable, "text", config.MediaRoot, seat.session, seat.compositor)
	command.Env = []string{"PATH=/usr/bin:/bin", "LANG=C", "HOME=/nonexistent"}
	command.ExtraFiles = []*os.File{child}
	command.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: seat.uid, Gid: bus.Gid}}
	if err := command.Start(); err != nil {
		cancel()
		parent.Close()
		return nil, err
	}
	return &loginTextWorker{connection: parent, reader: bufio.NewReader(parent), child: command, cancel: cancel}, nil
}

func (w *loginTextWorker) paste(id uint64, text string, prepared func()) string {
	_ = w.connection.SetDeadline(time.Now().Add(2 * time.Second))
	if WriteHostDesktopMessage(w.connection, HostDesktopMessage{Version: 1, ID: id, Type: "clipboard", Text: &text}) != nil {
		return "CLIPBOARD_UNAVAILABLE"
	}
	message, err := ReadHostDesktopMessage(w.reader)
	ready := false
	if err == nil && message.ID == id && message.Type == "result" && message.Code == "TEXT_SELECTION_READY" {
		ready = true
		prepared()
		message, err = ReadHostDesktopMessage(w.reader)
	}
	if err != nil || message.ID != id || (message.Type != "result" && message.Type != "error") {
		return "CLIPBOARD_UNAVAILABLE"
	}
	if message.Type == "error" {
		return message.Code
	}
	if !ready {
		return "CLIPBOARD_UNAVAILABLE"
	}
	return ""
}

func (w *loginTextWorker) close() {
	w.cancel()
	_ = w.connection.Close()
	_ = w.child.Wait()
}

func RunLoginTextPython(root, session, compositor string) error {
	return loginExecPython(root, "host_desktop_login_text.py", []string{session, compositor})
}
