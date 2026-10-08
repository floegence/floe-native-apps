//go:build linux

package hostdesktop

import (
	"crypto/sha256"
	"io"
	"net"
	"os"
	"path/filepath"
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
