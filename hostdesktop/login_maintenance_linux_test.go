//go:build linux

package hostdesktop

import (
	"bufio"
	"context"
	"net"
	"testing"
	"time"
)

func TestLoginMaintenanceCountsAllAttachmentsAndBlocksAdmission(t *testing.T) {
	server := &loginServer{}
	first, second, late := &net.UnixConn{}, &net.UnixConn{}, &net.UnixConn{}
	if !server.admitAttachment(first) || !server.admitAttachment(second) {
		t.Fatal("initial admission failed")
	}
	path := t.TempDir() + "/maintenance.sock"
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	done := make(chan struct{})
	go func() {
		defer close(done)
		conn, err := listener.AcceptUnix()
		if err == nil {
			defer conn.Close()
			server.serveMaintenance(context.Background(), conn)
		}
	}()
	conn, err := net.DialUnix("unix", nil, &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	if err := loginWritePacket(conn, loginServiceHello{Version: loginAttachmentVersion, Operation: "maintenance"}); err != nil {
		t.Fatal(err)
	}
	var reply loginServiceReply
	if err := loginReadPacket(bufio.NewReader(conn), &reply); err != nil {
		t.Fatal(err)
	}
	if reply.Maintenance == nil || reply.Maintenance.ActiveConnections != 2 {
		t.Fatal("view and control attachments not counted")
	}
	if server.admitAttachment(late) {
		t.Fatal("admitted attachment under lease")
	}
	server.releaseAttachment(first)
	server.mu.Lock()
	count := len(server.attachments)
	server.mu.Unlock()
	if count != 1 {
		t.Fatal("existing attachment was interrupted")
	}
	_ = conn.Close()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("lease not released")
	}
	if !server.admitAttachment(late) {
		t.Fatal("lease release did not restore admission")
	}
}

func TestLoginRootRuntimeMediaIdentityDropsRoot(t *testing.T) {
	uid, gid := loginMediaIdentity(0, 0)
	if uid == 0 || gid == 0 {
		t.Fatal("root Runtime retained root media authority")
	}
	uid, gid = loginMediaIdentity(1000, 1001)
	if uid != 1000 || gid != 1001 {
		t.Fatal("ordinary Runtime media identity changed")
	}
}
