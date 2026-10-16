//go:build linux

package hostdesktop

import (
	"bufio"
	"context"
	"errors"
	"golang.org/x/sys/unix"
	"net"
	"os"
	"time"
)

func loginRootPeer(conn *net.UnixConn) bool {
	raw, err := conn.SyscallConn()
	if err != nil {
		return false
	}
	var cred *unix.Ucred
	var peerErr error
	err = raw.Control(func(fd uintptr) { cred, peerErr = unix.GetsockoptUcred(int(fd), unix.SOL_SOCKET, unix.SO_PEERCRED) })
	return err == nil && peerErr == nil && cred != nil && cred.Uid == 0
}
func (s *loginServer) admitAttachment(conn *net.UnixConn) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.maintenance != nil || s.pendingDeployment() {
		return false
	}
	if s.attachments == nil {
		s.attachments = map[*net.UnixConn]struct{}{}
	}
	s.attachments[conn] = struct{}{}
	return true
}
func (s *loginServer) releaseAttachment(conn *net.UnixConn) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.attachments, conn)
	if s.controller == conn {
		s.controller = nil
	}
}
func (s *loginServer) serveMaintenance(ctx context.Context, conn *net.UnixConn) {
	var hello loginServiceHello
	if loginReadPacket(bufio.NewReader(conn), &hello) != nil || hello.Operation != "maintenance" || hello.Version != loginAttachmentVersion {
		return
	}
	s.serveMaintenanceHello(ctx, conn, hello)
}
func (s *loginServer) serveMaintenanceHello(ctx context.Context, conn *net.UnixConn, hello loginServiceHello) {
	if hello.Operation != "maintenance" || hello.Version != loginAttachmentVersion {
		return
	}
	s.mu.Lock()
	if s.maintenance != nil {
		s.mu.Unlock()
		_ = loginWritePacket(conn, loginServiceReply{Version: loginAttachmentVersion, Code: "DEPLOYMENT_IN_PROGRESS"})
		return
	}
	s.maintenance = conn
	count := len(s.attachments)
	s.mu.Unlock()
	defer func() {
		s.mu.Lock()
		if s.maintenance == conn {
			s.maintenance = nil
		}
		s.mu.Unlock()
	}()
	if loginWritePacket(conn, loginServiceReply{Version: loginAttachmentVersion, Maintenance: &loginMaintenanceStatus{ActiveConnections: count, Generation: loginChallenge()}}) != nil {
		return
	}
	_ = conn.SetDeadline(time.Time{})
	stop := context.AfterFunc(ctx, func() { _ = conn.Close() })
	defer stop()
	// The lease survives existing attachment exits and responds only to
	// bounded continuity checks from its authenticated root owner.
	for {
		var check loginServiceHello
		if loginReadPacket(bufio.NewReader(conn), &check) != nil || check.Version != loginAttachmentVersion || check.Operation != "maintenance_check" {
			return
		}
		if loginWritePacket(conn, loginServiceReply{Version: loginAttachmentVersion, Code: "MAINTENANCE_HELD"}) != nil {
			return
		}
	}

}
func loginAcquireMaintenance(ctx context.Context) (*net.UnixConn, *loginMaintenanceStatus, error) {
	socket, reader, err := loginDial(ctx, LoginServiceSocket)
	if err != nil {
		return nil, nil, err
	}
	conn := socket.(*net.UnixConn)
	if err = loginWritePacket(conn, loginServiceHello{Version: loginAttachmentVersion, Operation: "maintenance"}); err != nil {
		_ = conn.Close()
		return nil, nil, err
	}
	var reply loginServiceReply
	if err = loginReadPacket(reader, &reply); err != nil || reply.Version != loginAttachmentVersion || reply.Maintenance == nil {
		_ = conn.Close()
		return nil, nil, ErrServiceUpdateRequired
	}
	_ = conn.SetDeadline(time.Time{})
	return conn, reply.Maintenance, nil
}

func (s *loginServer) pendingDeployment() bool {
	if s.config.DeploymentJournal == "" {
		return false
	}
	_, err := os.Lstat(s.config.DeploymentJournal)
	return !errors.Is(err, os.ErrNotExist)
}
