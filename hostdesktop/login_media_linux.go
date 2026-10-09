//go:build linux

package hostdesktop

import (
	"bufio"
	"context"
	"errors"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sync"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

// Media owns capture and codec credits. The attachment owner alone admits
// frames and painted receipts; neither worker output nor a writable FD grants input.
type loginMediaWorker struct {
	commands    chan any
	interaction chan uint64
	messages    chan HostDesktopMessage
	done        chan struct{}
	cancel      context.CancelFunc
	children    []*exec.Cmd
	files       []*os.File
	threads     sync.WaitGroup
}

func loginMediaTrusted(root string) error {
	if !filepath.IsAbs(root) || root != filepath.Clean(root) || !loginRootOwnedDirectory(root) {
		return errLoginPeerRejected
	}
	return filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return errLoginPeerRejected
		}
		var stat unix.Stat_t
		if unix.Lstat(path, &stat) != nil || stat.Uid != 0 || stat.Mode&0022 != 0 ||
			entry.Type()&os.ModeSymlink != 0 || (!entry.IsDir() && !entry.Type().IsRegular()) {
			return errLoginPeerRejected
		}
		return nil
	})
}

func openLoginMedia(ctx context.Context, config LoginServiceConfig) (*loginMediaWorker, error) {
	if config.RuntimeUID == 0 || loginRootOwnedExecutable(config.WorkerPath) != nil || loginMediaTrusted(config.MediaRoot) != nil {
		return nil, errLoginPeerRejected
	}
	owned, cancel := context.WithCancel(ctx)
	w := &loginMediaWorker{commands: make(chan any, 8), interaction: make(chan uint64, 1), messages: make(chan HostDesktopMessage, 8), done: make(chan struct{}), cancel: cancel}
	success := false
	defer func() {
		if !success {
			w.close()
		}
	}()
	exporter, child, err := loginSocketPair()
	if err != nil {
		return nil, err
	}
	defer exporter.Close()
	defer child.Close()
	shared, err := exporter.File()
	if err != nil {
		return nil, err
	}
	defer shared.Close()
	command := exec.CommandContext(owned, config.WorkerPath, "export")
	command.Env = []string{"PATH=/usr/bin:/bin", "LANG=C", "HOME=/nonexistent"}
	command.ExtraFiles = []*os.File{child}
	if err = command.Start(); err != nil {
		return nil, errors.Join(errLoginExporterStart, err)
	}
	w.children = append(w.children, command)
	controlChild, control, err := os.Pipe()
	if err != nil {
		return nil, err
	}
	w.files = append(w.files, control)
	defer controlChild.Close()
	output, outputChild, err := os.Pipe()
	if err != nil {
		return nil, err
	}
	w.files = append(w.files, output)
	defer outputChild.Close()
	service := filepath.Join(filepath.Dir(config.WorkerPath), "floe-host-desktop-service")
	if loginRootOwnedExecutable(service) != nil {
		return nil, errLoginPeerRejected
	}
	command = exec.CommandContext(owned, service, "media", config.MediaRoot, config.WorkerPath)
	command.Env = []string{"PATH=/usr/bin:/bin", "LANG=C", "HOME=/nonexistent"}
	command.ExtraFiles = []*os.File{shared, controlChild}
	command.Stdout = outputChild
	command.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: config.RuntimeUID, Gid: config.RuntimeGID, Groups: loginRenderGroups(config.RuntimeGID)}}
	if err = command.Start(); err != nil {
		return nil, errors.Join(errLoginConverterStart, err)
	}
	w.children = append(w.children, command)
	w.threads.Add(2)
	go func() {
		defer w.threads.Done()
		defer cancel()
		for {
			var packet any
			select {
			case <-owned.Done():
				return
			case packet = <-w.commands:
			case generation := <-w.interaction:
				packet = map[string]any{"method": "interacted", "generation": generation}
			}
			_ = control.SetWriteDeadline(time.Now().Add(time.Second))
			if loginWritePacket(control, packet) != nil {
				return
			}
		}
	}()
	go func() {
		defer w.threads.Done()
		defer cancel()
		reader := bufio.NewReader(output)
		for {
			message, err := ReadHostDesktopMessage(reader)
			if err != nil {
				return
			}
			select {
			case w.messages <- message:
			case <-owned.Done():
				return
			}
		}
	}()
	go func() {
		<-owned.Done()
		close(w.done)
		for _, file := range w.files {
			_ = file.Close()
		}
	}()
	success = true
	return w, nil
}

func (w *loginMediaWorker) interacted(generation uint64) {
	select {
	case <-w.interaction:
	default:
	}
	select {
	case w.interaction <- generation:
	default:
	}
}

// RunLoginMediaPython executes only the immutable installation selected by the
// daemon. Thread pinning keeps capability removal and exec on the same thread.
func RunLoginMediaPython(root, worker string) error {
	if os.Geteuid() == 0 {
		return errLoginPeerRejected
	}
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
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
	if unix.Prctl(unix.PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != nil {
		return errLoginPeerRejected
	}
	base := filepath.Join(root, "floe", "host-desktop")
	python := filepath.Join(base, "python3")
	return syscall.Exec(python, []string{python, filepath.Join(base, "host_desktop_drm.py"), "--worker", worker}, []string{"PATH=/usr/bin:/bin", "LANG=C", "HOME=/nonexistent"})
}

func (w *loginMediaWorker) send(command any) bool {
	select {
	case <-w.done:
		return false
	case w.commands <- command:
		return true
	default:
		w.cancel()
		return false
	}
}

func (w *loginMediaWorker) close() {
	w.cancel()
	for _, file := range w.files {
		_ = file.Close()
	}
	w.threads.Wait()
	for _, child := range w.children {
		_ = child.Wait()
	}
}

// Never drop encoded reference frames. Backpressure ends the attachment and
// releases input; the bounded writer cannot stall the authority dispatch loop.
func writeLoginMedia(ctx context.Context, conn *net.UnixConn, packets <-chan HostDesktopMessage, cancel context.CancelFunc) {
	defer cancel()
	for {
		select {
		case <-ctx.Done():
			return
		case packet := <-packets:
			_ = conn.SetWriteDeadline(time.Now().Add(3 * time.Second))
			if WriteHostDesktopMessage(conn, packet) != nil {
				return
			}
		}
	}
}
