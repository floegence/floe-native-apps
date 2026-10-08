//go:build linux

package hostdesktop

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"errors"
	"io"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/sys/unix"
)

var errLoginPeerRejected = errors.New("login-screen peer rejected")

func loginRootOwnedExecutable(path string) error {
	if !filepath.IsAbs(path) || path != filepath.Clean(path) {
		return errLoginPeerRejected
	}
	for p := filepath.Clean(path); ; p = filepath.Dir(p) {
		info, err := os.Lstat(p)
		if err != nil {
			return errLoginPeerRejected
		}
		// os.FileInfo.Sys is syscall.Stat_t, whose type aliases vary across Go
		// platforms; use lstat directly for the Linux ownership boundary.
		var native unix.Stat_t
		if err = unix.Lstat(p, &native); err != nil {
			return errLoginPeerRejected
		}
		if native.Uid != 0 || info.Mode()&os.ModeSymlink != 0 || info.Mode().Perm()&0022 != 0 {
			return errLoginPeerRejected
		}
		if p == path && (!info.Mode().IsRegular() || info.Mode().Perm()&0111 == 0) {
			return errLoginPeerRejected
		}
		if p == "/" {
			break
		}
	}
	return nil
}

type loginPeerIdentity struct {
	uid, pid uint32
	start    string
	digest   [32]byte
	dev      uint64
	ino      uint64
}

func loginPeer(conn *net.UnixConn, uid uint32, expected [32]byte) (loginPeerIdentity, error) {
	var identity loginPeerIdentity
	raw, err := conn.SyscallConn()
	if err != nil {
		return identity, errLoginPeerRejected
	}
	var credential *unix.Ucred
	var credentialErr error
	if err = raw.Control(func(fd uintptr) {
		credential, credentialErr = unix.GetsockoptUcred(int(fd), unix.SOL_SOCKET, unix.SO_PEERCRED)
	}); err != nil || credentialErr != nil || credential == nil || credential.Pid <= 0 || credential.Uid != uid {
		return identity, errLoginPeerRejected
	}
	identity.uid, identity.pid = credential.Uid, uint32(credential.Pid)
	start, err := loginProcessStart(identity.pid)
	if err != nil {
		return identity, errLoginPeerRejected
	}
	identity.start = start
	// The live executable descriptor binds the process image even if its launch
	// pathname was replaced during a Runtime update.
	file, err := os.Open(loginProcessPath(identity.pid, "exe"))
	if err != nil {
		return identity, errLoginPeerRejected
	}
	defer file.Close()
	var imageStat unix.Stat_t
	if unix.Fstat(int(file.Fd()), &imageStat) != nil {
		return identity, errLoginPeerRejected
	}
	identity.dev, identity.ino = uint64(imageStat.Dev), imageStat.Ino
	hash := sha256.New()
	if _, err = io.Copy(hash, io.LimitReader(file, 512<<20)); err != nil {
		return identity, errLoginPeerRejected
	}
	copy(identity.digest[:], hash.Sum(nil))
	if subtle.ConstantTimeCompare(identity.digest[:], expected[:]) != 1 {
		return identity, errLoginPeerRejected
	}
	after, err := loginProcessStart(identity.pid)
	if err != nil || after != start {
		return identity, errLoginPeerRejected
	}
	return identity, nil
}
func loginProcessPath(pid uint32, name string) string {
	return filepath.Join("/proc", strconv.FormatUint(uint64(pid), 10), name)
}
func loginProcessStart(pid uint32) (string, error) {
	data, err := os.ReadFile(loginProcessPath(pid, "stat"))
	if err != nil {
		return "", err
	}
	// comm may contain spaces and parentheses. Fields after its closing paren
	// start at field 3, making starttime (22) the twentieth remaining field.
	closing := strings.LastIndexByte(string(data), ')')
	if closing < 0 {
		return "", errLoginPeerRejected
	}
	fields := strings.Fields(string(data[closing+1:]))
	if len(fields) < 20 {
		return "", errLoginPeerRejected
	}
	return fields[19], nil
}

type loginTicket struct {
	peer    loginPeerIdentity
	expires time.Time
}
type loginTickets struct {
	mu     sync.Mutex
	values map[string]loginTicket
	now    func() time.Time
}

func (t *loginTickets) issue(peer loginPeerIdentity) (string, error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.now == nil {
		t.now = time.Now
	}
	now := t.now()
	if t.values == nil {
		t.values = map[string]loginTicket{}
	}
	for token, value := range t.values {
		if !now.Before(value.expires) {
			delete(t.values, token)
		}
	}
	if len(t.values) >= 16 {
		return "", errLoginPeerRejected
	}
	secret := make([]byte, 32)
	if _, err := rand.Read(secret); err != nil {
		return "", err
	}
	token := hex.EncodeToString(secret)
	t.values[token] = loginTicket{peer: peer, expires: now.Add(5 * time.Second)}
	return token, nil
}
func (t *loginTickets) consume(token string, peer loginPeerIdentity) bool {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.now == nil {
		t.now = time.Now
	}
	ticket, ok := t.values[token]
	delete(t.values, token)
	return ok && len(token) == 64 && t.now().Before(ticket.expires) && ticket.peer == peer
}

// The client also authenticates the daemon before sending attachment material.
func loginServerIdentity(conn net.Conn) error {
	socket, ok := conn.(*net.UnixConn)
	if !ok {
		return errLoginPeerRejected
	}
	raw, err := socket.SyscallConn()
	if err != nil {
		return errLoginPeerRejected
	}
	var credential *unix.Ucred
	var credentialErr error
	if err = raw.Control(func(fd uintptr) {
		credential, credentialErr = unix.GetsockoptUcred(int(fd), unix.SOL_SOCKET, unix.SO_PEERCRED)
	}); err != nil || credentialErr != nil || credential == nil || credential.Uid != 0 {
		return errLoginPeerRejected
	}
	return nil
}
