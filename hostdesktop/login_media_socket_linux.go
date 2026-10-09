//go:build linux

package hostdesktop

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"io"
	"net"
	"os"

	"golang.org/x/sys/unix"
)

func loginMediaSocketPair() (*net.UnixConn, *os.File, error) {
	fds, err := unix.Socketpair(unix.AF_UNIX, unix.SOCK_STREAM|unix.SOCK_CLOEXEC, 0)
	if err != nil {
		return nil, nil, err
	}
	parent := os.NewFile(uintptr(fds[0]), "desktop-media-parent")
	child := os.NewFile(uintptr(fds[1]), "desktop-media-child")
	connection, err := net.FileConn(parent)
	_ = parent.Close()
	if err != nil {
		_ = child.Close()
		return nil, nil, err
	}
	return connection.(*net.UnixConn), child, nil
}

func writeLoginAttachment(conn *net.UnixConn, media *os.File) error {
	data, err := json.Marshal(loginServiceReply{Version: loginAttachmentVersion})
	if err != nil {
		return err
	}
	var prefix [4]byte
	binary.BigEndian.PutUint32(prefix[:], uint32(len(data)))
	n, _, err := conn.WriteMsgUnix(prefix[:], unix.UnixRights(int(media.Fd())), nil)
	if err != nil {
		return err
	}
	if n != 4 {
		return io.ErrShortWrite
	}
	_, err = io.Copy(conn, bytes.NewReader(data))
	return err
}

func readLoginAttachment(conn *net.UnixConn) (*net.UnixConn, error) {
	var prefix [4]byte
	control := make([]byte, unix.CmsgSpace(8*4))
	n, oob, flags, _, err := conn.ReadMsgUnix(prefix[:], control)
	var fds []int
	defer func() {
		for _, fd := range fds {
			_ = unix.Close(fd)
		}
	}()
	messages, parseErr := unix.ParseSocketControlMessage(control[:oob])
	for _, message := range messages {
		rights, e := unix.ParseUnixRights(&message)
		if e != nil {
			parseErr = e
		} else {
			fds = append(fds, rights...)
		}
	}
	if err != nil || parseErr != nil || flags&(unix.MSG_TRUNC|unix.MSG_CTRUNC) != 0 || n == 0 {
		return nil, ErrHostDesktopProtocol
	}
	if _, err := io.ReadFull(conn, prefix[n:]); err != nil {
		return nil, err
	}
	size := binary.BigEndian.Uint32(prefix[:])
	if size == 0 || size > 4096 {
		return nil, ErrHostDesktopProtocol
	}
	data := make([]byte, size)
	if _, err := io.ReadFull(conn, data); err != nil {
		return nil, err
	}
	var reply loginServiceReply
	if !hostDesktopUniqueJSON(data) || json.Unmarshal(data, &reply) != nil {
		return nil, ErrHostDesktopProtocol
	}
	if reply.Version != loginAttachmentVersion {
		return nil, ErrServiceUpdateRequired
	}
	if reply.Code == "SERVICE_UPDATE_REQUIRED" {
		return nil, ErrServiceUpdateRequired
	}
	if reply.Code != "" || len(fds) != 1 {
		return nil, ErrHostDesktopProtocol
	}
	unix.CloseOnExec(fds[0])
	typeOf, err := unix.GetsockoptInt(fds[0], unix.SOL_SOCKET, unix.SO_TYPE)
	if err != nil || typeOf != unix.SOCK_STREAM {
		return nil, ErrHostDesktopProtocol
	}
	file := os.NewFile(uintptr(fds[0]), "desktop-media")
	connection, err := net.FileConn(file)
	_ = file.Close()
	fds = nil
	if err != nil {
		return nil, err
	}
	media, ok := connection.(*net.UnixConn)
	if !ok {
		_ = connection.Close()
		return nil, ErrHostDesktopProtocol
	}
	if err := loginServerIdentity(media); err != nil {
		_ = media.Close()
		return nil, err
	}
	return media, nil
}
