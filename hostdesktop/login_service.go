package hostdesktop

import (
	"bufio"
	"bytes"
	"encoding/binary"
	"encoding/json"
	"io"
)

const LoginServiceSocket = "/run/redeven-desktop/desktop.sock"

// LoginServiceConfig contains administrator-installed, root-owned policy only.
// It is never populated from a viewer request. No credential belongs here.
type LoginServiceConfig struct {
	SocketPath             string
	WorkerPath             string
	RuntimeUID, RuntimeGID uint32
	RuntimeSHA256          string
	Seat                   string
}

type loginServiceHello struct {
	Operation string `json:"operation"`
	Token     string `json:"token,omitempty"`
}
type loginServiceReply struct {
	Code   string         `json:"code,omitempty"`
	Token  string         `json:"token,omitempty"`
	Status *ServiceStatus `json:"status,omitempty"`
}

func loginWritePacket(writer io.Writer, value any) error {
	data, err := json.Marshal(value)
	if err != nil {
		return err
	}
	if len(data) > 4096 {
		return ErrHostDesktopProtocol
	}
	prefix := make([]byte, 4)
	binary.BigEndian.PutUint32(prefix, uint32(len(data)))
	for _, part := range [][]byte{prefix, data} {
		for len(part) > 0 {
			n, e := writer.Write(part)
			if e != nil {
				return e
			}
			if n == 0 {
				return io.ErrShortWrite
			}
			part = part[n:]
		}
	}
	return nil
}
func loginReadPacket(reader *bufio.Reader, value any) error {
	prefix := make([]byte, 4)
	if _, err := io.ReadFull(reader, prefix); err != nil {
		return err
	}
	size := binary.BigEndian.Uint32(prefix)
	if size == 0 || size > 4096 {
		return ErrHostDesktopProtocol
	}
	data := make([]byte, size)
	if _, err := io.ReadFull(reader, data); err != nil {
		return err
	}
	if !hostDesktopUniqueJSON(data) {
		return ErrHostDesktopProtocol
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(value); err != nil {
		return ErrHostDesktopProtocol
	}
	return nil
}

func readLoginDesktopCommand(reader io.Reader) (HostDesktopCommand, error) {
	prefix := make([]byte, 4)
	if _, err := io.ReadFull(reader, prefix); err != nil {
		return HostDesktopCommand{}, err
	}
	size := binary.BigEndian.Uint32(prefix)
	if size == 0 || size > hostDesktopHeaderLimit {
		return HostDesktopCommand{}, ErrHostDesktopProtocol
	}
	data := make([]byte, size)
	if _, err := io.ReadFull(reader, data); err != nil {
		return HostDesktopCommand{}, err
	}
	return ParseHostDesktopCommand(data)
}
