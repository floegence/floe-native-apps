package nativeapps

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"net"
	"sync"
	"time"
	"unicode/utf8"
)

const (
	desktopRequestLimit = 128 * 1024
	desktopMessageLimit = 2 * 1024 * 1024
	desktopFrameLimit   = 4096 * 4096 * 4
	desktopMaxID        = 1<<53 - 1
)

// ErrDesktopProtocol reports invalid helper framing or metadata, without
// including application content, input text, or authentication material.
var ErrDesktopProtocol = errors.New("invalid native desktop helper protocol")

// DesktopEndpoint identifies an existing private helper. A trusted application
// instance supplies these values; they are never taken from viewer input.
type DesktopEndpoint struct {
	SocketPath string
	Instance   string
	Token      string
}

// DesktopConnection owns one authenticated helper attachment. It has no process
// owner, reconnect loop, frame queue, input scheduler, or implicit frame ack.
// One goroutine reads events while writers may send requests concurrently.
// Native validation and ordering remain authoritative in the helper.
type DesktopConnection struct {
	conn       net.Conn
	writeMu    sync.Mutex
	sequence   uint64
	connection uint64
}

// DesktopState is native observation, not application lifetime. In particular,
// waiting or unavailable must never be interpreted as application exit.
type DesktopState struct {
	State      string          `json:"state"`
	Window     uint64          `json:"window"`
	Generation uint64          `json:"generation"`
	Windows    []DesktopWindow `json:"windows"`
}

type DesktopWindow struct {
	Window      uint64 `json:"window"`
	Parent      uint64 `json:"parent"`
	Protocol    string `json:"protocol"`
	Width       int    `json:"width"`
	Height      int    `json:"height"`
	Title       string `json:"title"`
	Maximized   bool   `json:"maximized"`
	Fullscreen  bool   `json:"fullscreen"`
	Minimized   bool   `json:"minimized"`
	Interacting bool   `json:"interacting"`
}

// DesktopFrame binds pixels to the native connection, window and geometry
// generation. Receiving or parsing it does not grant input permission: the
// viewer must decode and paint it before sending frame_ack for Sequence.
type DesktopFrame struct {
	Encoding   string `json:"encoding"`
	Width      int    `json:"width"`
	Height     int    `json:"height"`
	Sequence   uint64 `json:"sequence"`
	Connection uint64 `json:"connection"`
	Window     uint64 `json:"window"`
	Generation uint64 `json:"generation"`
}

// DesktopEvent contains one complete helper event or request result. A frame
// event includes its bounded PNG payload. The caller owns those returned bytes.
// Result and Error preserve helper semantics: submitted is not a widget receipt.
type DesktopEvent struct {
	Event      string          `json:"event,omitempty"`
	ID         uint64          `json:"id,omitempty"`
	Version    int             `json:"version,omitempty"`
	Connection uint64          `json:"connection,omitempty"`
	State      *DesktopState   `json:"state,omitempty"`
	Frame      *DesktopFrame   `json:"frame,omitempty"`
	Bytes      int             `json:"bytes,omitempty"`
	Code       string          `json:"code,omitempty"`
	Error      string          `json:"error,omitempty"`
	Result     json.RawMessage `json:"result,omitempty"`
	Pixels     []byte          `json:"-"`
}

// DesktopRequest names a version-1 helper operation. Operation is the native
// input object for input requests; the helper validates it against the current
// acknowledged target. Other methods have no Operation. Target fields remain
// explicit so a stale callback cannot borrow the connection's current target.
type DesktopRequest struct {
	Method     string          `json:"method"`
	Connection uint64          `json:"connection,omitempty"`
	Window     uint64          `json:"window,omitempty"`
	Generation uint64          `json:"generation,omitempty"`
	Frame      uint64          `json:"frame,omitempty"`
	Operation  json.RawMessage `json:"operation,omitempty"`
}

func desktopID(value uint64) bool { return value > 0 && value <= desktopMaxID }

func (r DesktopRequest) valid(connection uint64) bool {
	switch r.Method {
	case "status", "refresh":
		return r.Connection == 0 && r.Window == 0 && r.Generation == 0 && r.Frame == 0 && len(r.Operation) == 0
	case "frame_ack":
		return desktopID(r.Frame) && r.Connection == 0 && r.Window == 0 && r.Generation == 0 && len(r.Operation) == 0
	case "select_window", "close_window":
		return desktopID(r.Window) && r.Connection == 0 && r.Generation == 0 && r.Frame == 0 && len(r.Operation) == 0
	case "input":
		return r.Connection == connection && desktopID(r.Window) && desktopID(r.Generation) && r.Frame == 0 &&
			len(r.Operation) > 0 && utf8.Valid(r.Operation) && json.Valid(r.Operation)
	default:
		return false
	}
}

// Connection returns the immutable attachment generation assigned by the
// helper. Reattachment creates another DesktopConnection and another generation.
func (c *DesktopConnection) Connection() uint64 { return c.connection }

// Close revokes this sharing attachment. It never terminates the helper, display
// or application. The helper releases input owned by the detached attachment.
func (c *DesktopConnection) Close() error { return c.conn.Close() }

// Send writes one bounded request and returns its sequence. The reply is read by
// Read. Cancellation during I/O closes this attachment; no request is replayed.
// A caller should use a separate continuous reader so frame delivery cannot
// prevent it from consuming replies. Input ordering belongs to the helper.
func (c *DesktopConnection) Send(ctx context.Context, request DesktopRequest) (uint64, error) {
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	if err := ctx.Err(); err != nil {
		return 0, err
	}
	if !request.valid(c.connection) || c.sequence >= desktopMaxID {
		return 0, ErrInvalid
	}
	message := struct {
		ID uint64 `json:"id"`
		DesktopRequest
	}{c.sequence + 1, request}
	data, err := json.Marshal(message)
	if err != nil || len(data) > desktopRequestLimit {
		return 0, ErrInvalid
	}
	c.sequence++
	stop := context.AfterFunc(ctx, func() { _ = c.conn.Close() })
	defer stop()
	if err := writeDesktopPacket(c.conn, data); err != nil {
		_ = c.Close()
		if ctx.Err() != nil {
			return 0, ctx.Err()
		}
		return 0, err
	}
	return c.sequence, nil
}

// Read returns one complete event without buffering subsequent frames or
// acknowledging pixels. Only one reader may call Read at a time. Cancellation
// closes the attachment rather than retaining a partially consumed packet.
func (c *DesktopConnection) Read(ctx context.Context) (DesktopEvent, error) {
	if err := ctx.Err(); err != nil {
		return DesktopEvent{}, err
	}
	stop := context.AfterFunc(ctx, func() { _ = c.conn.Close() })
	defer stop()
	event, err := c.read()
	if err != nil {
		_ = c.Close()
		if ctx.Err() != nil {
			return DesktopEvent{}, ctx.Err()
		}
		return DesktopEvent{}, err
	}
	return event, nil
}

func (c *DesktopConnection) read() (DesktopEvent, error) {
	body, err := readDesktopPacket(c.conn, 1, desktopMessageLimit)
	if err != nil {
		return DesktopEvent{}, err
	}
	var event DesktopEvent
	if !utf8.Valid(body) || json.Unmarshal(body, &event) != nil {
		return DesktopEvent{}, ErrDesktopProtocol
	}
	if event.Event == "" {
		if !desktopID(event.ID) || (event.Error == "") == (len(event.Result) == 0) || event.Frame != nil || event.Bytes != 0 {
			return DesktopEvent{}, ErrDesktopProtocol
		}
		return event, nil
	}
	if event.ID != 0 || len(event.Result) != 0 || event.Error != "" {
		return DesktopEvent{}, ErrDesktopProtocol
	}
	switch event.Event {
	case "attached":
		if c.connection != 0 || event.Version != 1 || !desktopID(event.Connection) || event.State == nil || event.Frame != nil || event.Bytes != 0 {
			return DesktopEvent{}, ErrDesktopProtocol
		}
	case "state":
		if event.Connection != c.connection || event.State == nil || event.Frame != nil || event.Bytes != 0 {
			return DesktopEvent{}, ErrDesktopProtocol
		}
	case "capture_unavailable":
		if event.Code != "CAPTURE_UNAVAILABLE" || event.Frame != nil || event.Bytes != 0 {
			return DesktopEvent{}, ErrDesktopProtocol
		}
	case "frame":
		f := event.Frame
		if f == nil || f.Encoding != "png" || f.Width < 1 || f.Width > 4096 || f.Height < 1 || f.Height > 4096 ||
			!desktopID(f.Sequence) || f.Connection != c.connection || !desktopID(f.Window) || !desktopID(f.Generation) ||
			event.Bytes < 45 || event.Bytes > desktopFrameLimit {
			return DesktopEvent{}, ErrDesktopProtocol
		}
		data, err := readDesktopPacket(c.conn, 2, event.Bytes)
		if err != nil {
			return DesktopEvent{}, err
		}
		if len(data) != event.Bytes || string(data[:16]) != "\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" ||
			binary.BigEndian.Uint32(data[16:20]) != uint32(f.Width) || binary.BigEndian.Uint32(data[20:24]) != uint32(f.Height) ||
			string(data[24:29]) != "\x08\x02\x00\x00\x00" {
			return DesktopEvent{}, ErrDesktopProtocol
		}
		event.Pixels = data
	default:
		return DesktopEvent{}, ErrDesktopProtocol
	}
	return event, nil
}

func readDesktopPacket(r io.Reader, kind byte, maximum int) ([]byte, error) {
	var header [5]byte
	if _, err := io.ReadFull(r, header[:]); err != nil {
		return nil, err
	}
	size := binary.BigEndian.Uint32(header[1:])
	if header[0] != kind || size == 0 || uint64(size) > uint64(maximum) {
		return nil, ErrDesktopProtocol
	}
	body := make([]byte, int(size))
	_, err := io.ReadFull(r, body)
	return body, err
}

func writeDesktopPacket(w io.Writer, body []byte) error {
	var header [5]byte
	header[0] = 1
	binary.BigEndian.PutUint32(header[1:], uint32(len(body)))
	for _, data := range [][]byte{header[:], body} {
		for len(data) > 0 {
			n, err := w.Write(data)
			if err != nil {
				return err
			}
			if n == 0 {
				return io.ErrShortWrite
			}
			data = data[n:]
		}
	}
	return nil
}

func authenticateDesktop(ctx context.Context, conn net.Conn, endpoint DesktopEndpoint) (*DesktopConnection, DesktopState, error) {
	c := &DesktopConnection{conn: conn}
	handshake, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	stop := context.AfterFunc(handshake, func() { _ = conn.Close() })
	defer stop()
	body, err := json.Marshal(map[string]any{"version": 1, "instance": endpoint.Instance, "token": endpoint.Token})
	if err == nil {
		err = writeDesktopPacket(conn, body)
	}
	var event DesktopEvent
	if err == nil {
		event, err = c.read()
	}
	if err == nil && event.Event != "attached" {
		err = ErrDesktopProtocol
	}
	if err != nil || handshake.Err() != nil {
		_ = conn.Close()
		if handshake.Err() != nil {
			err = handshake.Err()
		}
		return nil, DesktopState{}, err
	}
	c.connection = event.Connection
	return c, *event.State, nil
}
