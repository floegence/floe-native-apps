//go:build linux

package hostdesktop

import (
	"bufio"
	"context"
	"crypto/sha256"
	"io"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestLoginAttachmentTicketsSingleUseIdentityAndExpiry(t *testing.T) {
	now := time.Unix(1, 0)
	tickets := loginTickets{now: func() time.Time { return now }}
	peer := loginPeerIdentity{uid: 1000, pid: 42, start: "100", digest: [32]byte{1}, dev: 1, ino: 5}
	token, err := tickets.issue(peer)
	if err != nil {
		t.Fatal(err)
	}
	if !tickets.consume(token, peer) || tickets.consume(token, peer) {
		t.Fatal("ticket was not single use")
	}
	token, err = tickets.issue(peer)
	if err != nil {
		t.Fatal(err)
	}
	other := peer
	other.pid++
	if tickets.consume(token, other) || tickets.consume(token, peer) {
		t.Fatal("ticket not bound to original process")
	}
	token, err = tickets.issue(peer)
	if err != nil {
		t.Fatal(err)
	}
	now = now.Add(5 * time.Second)
	if tickets.consume(token, peer) {
		t.Fatal("expired ticket admitted")
	}
}
func TestLoginAttachmentTicketsBoundPendingRequests(t *testing.T) {
	tickets := loginTickets{}
	peer := loginPeerIdentity{uid: 1000}
	for i := 0; i < 16; i++ {
		if _, err := tickets.issue(peer); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := tickets.issue(peer); err == nil {
		t.Fatal("unbounded pending tickets")
	}
}
func TestLoginPeerPinsLiveExecutableAndUID(t *testing.T) {
	path := filepath.Join(t.TempDir(), "test.sock")
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	client, err := net.DialUnix("unix", nil, &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	server, err := listener.AcceptUnix()
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	file, err := os.Open("/proc/self/exe")
	if err != nil {
		t.Fatal(err)
	}
	hash := sha256.New()
	_, err = io.Copy(hash, file)
	_ = file.Close()
	if err != nil {
		t.Fatal(err)
	}
	var expected [32]byte
	copy(expected[:], hash.Sum(nil))
	peer, err := loginPeer(server, uint32(os.Getuid()), expected)
	if err != nil {
		t.Fatal(err)
	}
	if peer.pid != uint32(os.Getpid()) || peer.start == "" || peer.ino == 0 {
		t.Fatal("incomplete live process identity")
	}
	if _, err = loginPeer(server, uint32(os.Getuid())+1, expected); err == nil {
		t.Fatal("wrong UID admitted")
	}
	expected[0] ^= 1
	if _, err = loginPeer(server, uint32(os.Getuid()), expected); err == nil {
		t.Fatal("wrong executable admitted")
	}
}
func TestLoginExecutableRejectsWritableAncestorsAndSymlinks(t *testing.T) {
	path := filepath.Join(t.TempDir(), "worker")
	if err := os.WriteFile(path, []byte("fixture"), 0755); err != nil {
		t.Fatal(err)
	}
	if loginRootOwnedExecutable(path) == nil {
		t.Fatal("worker beneath writable directory admitted")
	}
	link := filepath.Join(t.TempDir(), "link")
	if err := os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	if loginRootOwnedExecutable(link) == nil {
		t.Fatal("worker symlink admitted")
	}
	if loginRootOwnedExecutable("relative/worker") == nil {
		t.Fatal("relative worker admitted")
	}
}

func TestLoginRootRuntimeReadOnlyAdmission(t *testing.T) {
	if os.Geteuid() != 0 {
		t.Skip("requires isolated Linux root fixture")
	}
	file, err := os.Open("/proc/self/exe")
	if err != nil {
		t.Fatal(err)
	}
	hash := sha256.New()
	_, err = io.Copy(hash, file)
	file.Close()
	if err != nil {
		t.Fatal(err)
	}
	server := &loginServer{config: LoginServiceConfig{RuntimeUID: 0, PolicyDigest: strings.Repeat("c", 64)}}
	copy(server.digest[:], hash.Sum(nil))
	path := filepath.Join(t.TempDir(), "root-runtime.sock")
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	go func() {
		conn, err := listener.AcceptUnix()
		if err == nil {
			server.serve(context.Background(), conn)
		}
	}()
	ctx, cancel := context.WithTimeout(t.Context(), time.Second)
	defer cancel()
	digest, err := VerifyLoginServiceAdmission(ctx, path, LoginServiceAdmissionProof{OperationID: "root-operation", ChallengeID: "root-challenge", PolicyDigest: server.config.PolicyDigest})
	if err != nil || digest != server.config.PolicyDigest {
		t.Fatalf("root Runtime proof: %s %v", digest, err)
	}
}

func TestLoginServiceRejectsUnversionedAdmission(t *testing.T) {
	server := &loginServer{}
	path := filepath.Join(t.TempDir(), "version.sock")
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	go func() {
		conn, e := listener.AcceptUnix()
		if e == nil {
			server.serve(t.Context(), conn)
		}
	}()
	conn, err := net.DialUnix("unix", nil, &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(time.Second))
	if err = loginWritePacket(conn, loginServiceHello{Operation: "status"}); err != nil {
		t.Fatal(err)
	}
	var reply loginServiceReply
	if err = loginReadPacket(bufio.NewReader(conn), &reply); err != nil || reply.Code != "SERVICE_UPDATE_REQUIRED" {
		t.Fatal("missing explicit version rejection", err, reply)
	}
}
