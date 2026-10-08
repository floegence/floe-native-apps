package hostdesktop

import (
	"bufio"
	"context"
	"errors"
	"net"
	"os"
	"runtime"
	"sync"
	"time"
)

func loginDial(ctx context.Context, path string) (net.Conn, *bufio.Reader, error) {
	if runtime.GOOS != "linux" {
		return nil, nil, ErrServiceUnsupported
	}
	if path == "" {
		path = LoginServiceSocket
	}
	socket, err := (&net.Dialer{Timeout: 3 * time.Second}).DialContext(ctx, "unix", path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, nil, ErrServiceNotInstalled
		}
		return nil, nil, err
	}
	deadline := time.Now().Add(5 * time.Second)
	if bound, ok := ctx.Deadline(); ok && bound.Before(deadline) {
		deadline = bound
	}
	_ = socket.SetDeadline(deadline)
	if err := loginServerIdentity(socket); err != nil {
		_ = socket.Close()
		return nil, nil, err
	}
	return socket, bufio.NewReader(socket), nil
}

// InstalledLoginServiceStatus is read-only. A running service is not a claim
// that any particular scanout layout is supported; attachment capture reports
// that independently, without requesting Portal consent or elevation.
func InstalledLoginServiceStatus(ctx context.Context, path string) (ServiceStatus, error) {
	conn, reader, err := loginDial(ctx, path)
	if err != nil {
		state := ServiceFailed
		backend := "linux-drm-kms"
		if errors.Is(err, ErrServiceNotInstalled) {
			state = ServiceNotInstalled
			if path == "" || path == LoginServiceSocket {
				if info, statErr := os.Lstat("/etc/systemd/system/redeven-desktop.service"); statErr == nil && info.Mode().IsRegular() {
					state = ServiceStopped
					err = nil
				}
			}
		}
		if errors.Is(err, ErrServiceUnsupported) {
			state = ServiceUnsupported
			backend = runtime.GOOS
		}
		return ServiceStatus{State: state, Backend: backend}, err
	}
	defer conn.Close()
	stopCancellation := context.AfterFunc(ctx, func() { _ = conn.Close() })
	defer stopCancellation()
	if err = loginWritePacket(conn, loginServiceHello{Operation: "status"}); err != nil {
		return ServiceStatus{}, err
	}
	var reply loginServiceReply
	if err = loginReadPacket(reader, &reply); err != nil || reply.Status == nil {
		return ServiceStatus{State: ServiceFailed, Reason: "service_identity_rejected", Backend: "linux-drm-kms"}, errLoginServiceUnavailable
	}
	return *reply.Status, nil
}

var errLoginServiceUnavailable = errors.New("login-screen attachment unavailable")

// LoginScreenConnection is the existing physical-desktop protocol carried over
// the local service socket. Authentication is a short-lived, single-use ticket
// tied to the live Runtime UID, PID, start time and administrator-pinned image.
// Close revokes the attachment, releasing its held kernel input devices.
type LoginScreenConnection struct {
	socket         net.Conn
	reader         *bufio.Reader
	control, media chan HostDesktopMessage
	done           chan struct{}
	once           sync.Once
	writeMu        sync.Mutex
}

func OpenLoginScreenSession(ctx context.Context, path string) (*LoginScreenConnection, error) {
	ticketConn, reader, err := loginDial(ctx, path)
	if err != nil {
		return nil, err
	}
	stopTicketCancellation := context.AfterFunc(ctx, func() { _ = ticketConn.Close() })
	defer stopTicketCancellation()
	if err = loginWritePacket(ticketConn, loginServiceHello{Operation: "ticket"}); err != nil {
		_ = ticketConn.Close()
		return nil, err
	}
	var ticket loginServiceReply
	err = loginReadPacket(reader, &ticket)
	_ = ticketConn.Close()
	if err != nil || len(ticket.Token) != 64 {
		return nil, errLoginServiceUnavailable
	}
	socket, reader, err := loginDial(ctx, path)
	if err != nil {
		return nil, err
	}
	stopAttachCancellation := context.AfterFunc(ctx, func() { _ = socket.Close() })
	defer stopAttachCancellation()
	if err = loginWritePacket(socket, loginServiceHello{Operation: "attach", Token: ticket.Token}); err != nil {
		_ = socket.Close()
		return nil, err
	}
	var reply loginServiceReply
	if err = loginReadPacket(reader, &reply); err != nil || reply.Code != "" {
		_ = socket.Close()
		return nil, errLoginServiceUnavailable
	}
	_ = socket.SetDeadline(time.Time{})
	connection := &LoginScreenConnection{socket: socket, reader: reader, control: make(chan HostDesktopMessage, 32), media: make(chan HostDesktopMessage, 4), done: make(chan struct{})}
	go connection.read()
	return connection, nil
}
func (c *LoginScreenConnection) read() {
	defer c.Close()
	for {
		message, err := ReadHostDesktopMessage(c.reader)
		if err != nil {
			return
		}
		destination := c.control
		if message.Type == "frame" || message.Type == "format" || message.Type == "audio" || message.Type == "cursor" {
			destination = c.media
		}
		select {
		case destination <- message:
		case <-c.done:
			return
		}
	}
}
func (c *LoginScreenConnection) Send(command HostDesktopCommand, _ bool) error {
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	_ = c.socket.SetWriteDeadline(time.Now().Add(3 * time.Second))
	return WriteHostDesktopCommand(c.socket, command)
}
func (c *LoginScreenConnection) Control() <-chan HostDesktopMessage { return c.control }
func (c *LoginScreenConnection) Media() <-chan HostDesktopMessage   { return c.media }
func (c *LoginScreenConnection) Done() <-chan struct{}              { return c.done }
func (c *LoginScreenConnection) Close() error {
	var err error
	c.once.Do(func() { err = c.socket.Close(); close(c.done) })
	return err
}
