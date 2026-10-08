package hostdesktop

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"math"
	"strings"
	"unicode/utf8"
)

// Physical desktop sessions are independent of private application desktops.
// Consumers authorize a session; native adapters own its current OS target.
const HostDesktopProtocolVersion = 1

const hostDesktopPayloadLimit = 64 << 20
const hostDesktopHeaderLimit = 8 << 20 // Includes worst-case escaping of 1 MiB text.

var ErrHostDesktopProtocol = errors.New("invalid host desktop protocol")

type HostDesktopDisplay struct {
	ID      string  `json:"id"`
	Name    string  `json:"name"`
	X       int     `json:"x"`
	Y       int     `json:"y"`
	Width   int     `json:"width"`
	Height  int     `json:"height"`
	Scale   float64 `json:"scale"`
	Primary bool    `json:"primary"`
}

type HostDesktopCapabilities struct {
	Authorization string               `json:"authorization,omitempty"`
	Backend       string               `json:"backend"`
	State         string               `json:"state"`
	Reason        string               `json:"reason,omitempty"`
	Screen        bool                 `json:"screen"`
	Input         bool                 `json:"input"`
	Clipboard     bool                 `json:"clipboard"`
	Audio         bool                 `json:"audio"`
	Unattended    bool                 `json:"unattended"`
	Unlock        bool                 `json:"unlock"`
	LockedScreen  bool                 `json:"locked_screen,omitempty"`
	Service       string               `json:"service,omitempty"`
	Encoder       string               `json:"encoder,omitempty"`
	Displays      []HostDesktopDisplay `json:"displays"`
}

type HostDesktopPicture struct {
	Mode         string `json:"mode"`
	MaxDimension int    `json:"max_dimension"`
	FrameRate    int    `json:"frame_rate"`
	Audio        bool   `json:"audio"`
	NativePixels bool   `json:"native_pixels,omitempty"`
}

// HostDesktopServiceStatus is the stable, redacted status of the optional
// login-screen service. It intentionally contains no installation path or
// credential material.
type HostDesktopServiceStatus struct {
	State   string `json:"state"`
	Reason  string `json:"reason,omitempty"`
	Backend string `json:"backend,omitempty"`
}

// HostDesktopInput uses normalized selected-display coordinates. Code names are
// physical KeyboardEvent.code names; text and clipboard are explicit operations.
type HostDesktopInput struct {
	Kind     string  `json:"kind"`
	X        float64 `json:"x"`
	Y        float64 `json:"y"`
	DX       float64 `json:"dx"`
	DY       float64 `json:"dy"`
	Button   int     `json:"button,omitempty"`
	Clicks   int     `json:"clicks,omitempty"`
	Key      string  `json:"key,omitempty"`
	Code     string  `json:"code,omitempty"`
	Pressed  bool    `json:"pressed,omitempty"`
	Repeat   bool    `json:"repeat,omitempty"`
	Location int     `json:"location,omitempty"`
	Shift    bool    `json:"shiftKey,omitempty"`
	Control  bool    `json:"ctrlKey,omitempty"`
	Alt      bool    `json:"altKey,omitempty"`
	Meta     bool    `json:"metaKey,omitempty"`
	Text     string  `json:"text,omitempty"`
}

type HostDesktopCommand struct {
	Version    int                 `json:"version"`
	ID         uint64              `json:"id"`
	Method     string              `json:"method"`
	Generation uint64              `json:"generation,omitempty"`
	FrameID    uint64              `json:"frame_id,omitempty"`
	DisplayID  string              `json:"display_id,omitempty"`
	Mode       string              `json:"mode,omitempty"`
	Unattended bool                `json:"unattended,omitempty"`
	Picture    *HostDesktopPicture `json:"picture,omitempty"`
	Input      *HostDesktopInput   `json:"input,omitempty"`
	Text       *string             `json:"text,omitempty"`
	Enabled    *bool               `json:"enabled,omitempty"`
	Service    string              `json:"service,omitempty"`
}

// HostDesktopMessage carries control metadata or one encoded media packet.
// Data is binary on media transports; credentials and content are not diagnostics.
type HostDesktopMessage struct {
	Authorization string                    `json:"authorization,omitempty"`
	Version       int                       `json:"version"`
	Type          string                    `json:"type"`
	ID            uint64                    `json:"id,omitempty"`
	Code          string                    `json:"code,omitempty"`
	State         string                    `json:"state,omitempty"`
	Mode          string                    `json:"mode,omitempty"`
	Capabilities  *HostDesktopCapabilities  `json:"capabilities,omitempty"`
	Service       string                    `json:"service,omitempty"`
	ServiceStatus *HostDesktopServiceStatus `json:"service_status,omitempty"`
	Displays      []HostDesktopDisplay      `json:"displays,omitempty"`
	DisplayID     string                    `json:"display_id,omitempty"`
	Generation    uint64                    `json:"generation,omitempty"`
	FrameID       uint64                    `json:"frame_id,omitempty"`
	Timestamp     int64                     `json:"timestamp,omitempty"`
	Codec         string                    `json:"codec,omitempty"`
	Profile       string                    `json:"profile,omitempty"`
	Description   string                    `json:"description,omitempty"`
	Encoder       string                    `json:"encoder,omitempty"`
	Cursor        string                    `json:"cursor,omitempty"`
	Width         int                       `json:"width,omitempty"`
	Height        int                       `json:"height,omitempty"`
	HotX          int                       `json:"hot_x,omitempty"`
	HotY          int                       `json:"hot_y,omitempty"`
	Key           bool                      `json:"key,omitempty"`
	SampleRate    int                       `json:"sample_rate,omitempty"`
	Channels      int                       `json:"channels,omitempty"`
	Text          *string                   `json:"text,omitempty"`
	Bytes         int                       `json:"bytes,omitempty"`
	Data          []byte                    `json:"-"`
}

func hostDesktopText(text string, maximum int) bool {
	return len(text) <= maximum && utf8.ValidString(text) && !strings.ContainsRune(text, 0)
}

func hostDesktopDisplayID(id string) bool {
	if id == "" || len(id) > 160 {
		return false
	}
	for _, ch := range id {
		if !(ch >= 'a' && ch <= 'z' || ch >= 'A' && ch <= 'Z' || ch >= '0' && ch <= '9' || ch == '-' || ch == '_') {
			return false
		}
	}
	return true
}

func hostDesktopServiceName(name string) bool { return name == "login-screen" }

func (p HostDesktopPicture) valid() bool {
	if p.Mode != "auto" && p.Mode != "clarity" && p.Mode != "smooth" && p.Mode != "data" {
		return false
	}
	dimension := p.MaxDimension == 1600 || p.MaxDimension == 1920 || p.MaxDimension == 2560 || p.MaxDimension == 3840 || p.MaxDimension == 4096
	return (p.FrameRate == 15 || p.FrameRate == 30 || p.FrameRate == 60) && dimension
}

func (input HostDesktopInput) valid() bool {
	for _, n := range []float64{input.X, input.Y, input.DX, input.DY} {
		if math.IsNaN(n) || math.IsInf(n, 0) {
			return false
		}
	}
	switch input.Kind {
	case "move", "down", "up", "scroll":
		return input.X >= 0 && input.X <= 1 && input.Y >= 0 && input.Y <= 1 && input.Button >= 0 && input.Button <= 4 && input.Clicks >= 0 && input.Clicks <= 3 && math.Abs(input.DX) <= 10000 && math.Abs(input.DY) <= 10000
	case "key":
		return input.Code != "" && hostDesktopText(input.Code, 64) && hostDesktopText(input.Key, 128) && input.Location >= 0 && input.Location <= 3
	case "text", "paste":
		return input.Text != "" && hostDesktopText(input.Text, 16000)
	}
	return false
}

func ParseHostDesktopCommand(data []byte) (HostDesktopCommand, error) {
	var command HostDesktopCommand
	if len(data) > hostDesktopHeaderLimit || !utf8.Valid(data) || !hostDesktopUniqueJSON(data) {
		return command, ErrHostDesktopProtocol
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&command) != nil || decoder.Decode(new(any)) != io.EOF || !command.Valid() {
		return HostDesktopCommand{}, ErrHostDesktopProtocol
	}
	return command, nil
}

func hostDesktopUniqueJSON(data []byte) bool {
	decoder := json.NewDecoder(bytes.NewReader(data))
	var read func(int) bool
	read = func(depth int) bool {
		if depth > 16 {
			return false
		}
		token, err := decoder.Token()
		if err != nil {
			return false
		}
		delim, ok := token.(json.Delim)
		if !ok {
			return true
		}
		if delim != '{' && delim != '[' {
			return false
		}
		seen := map[string]bool{}
		for decoder.More() {
			if delim == '{' {
				key, err := decoder.Token()
				name, ok := key.(string)
				if err != nil || !ok || seen[name] {
					return false
				}
				seen[name] = true
			}
			if !read(depth + 1) {
				return false
			}
		}
		end, err := decoder.Token()
		return err == nil && (delim == '{' && end == json.Delim('}') || delim == '[' && end == json.Delim(']'))
	}
	if !read(0) {
		return false
	}
	_, err := decoder.Token()
	return err == io.EOF
}

func (command HostDesktopCommand) Valid() bool {
	if command.Version != HostDesktopProtocolVersion || !desktopID(command.ID) {
		return false
	}
	if command.Picture != nil && (!command.Picture.valid() || command.Method != "connect" && command.Method != "configure") {
		return false
	}
	if command.Input != nil && command.Method != "input" && command.Method != "unlock_input" || command.Text != nil && command.Method != "set_clipboard" {
		return false
	}
	if command.Enabled != nil && command.Method != "set_clipboard_sync" {
		return false
	}
	if command.Mode != "" && command.Method != "connect" && command.Method != "set_mode" {
		return false
	}
	if command.Unattended && command.Method != "connect" {
		return false
	}
	if command.DisplayID != "" && (command.Method != "connect" && command.Method != "select_display" || !hostDesktopDisplayID(command.DisplayID)) {
		return false
	}
	if command.FrameID != 0 && command.Method != "frame_ack" && command.Method != "unlock_input" {
		return false
	}
	if command.Service != "" && command.Method != "service_status" && command.Method != "service_install" && command.Method != "service_uninstall" && command.Method != "login_session" {
		return false
	}
	switch command.Method {
	case "probe", "disconnect", "forget_authorization":
		return command.Generation == 0
	case "connect":
		return command.Generation == 0 && (command.Mode == "view" || command.Mode == "control") && command.Picture != nil
	case "configure":
		return desktopID(command.Generation) && command.Picture != nil
	case "select_display":
		return desktopID(command.Generation) && hostDesktopDisplayID(command.DisplayID)
	case "set_mode":
		return desktopID(command.Generation) && (command.Mode == "view" || command.Mode == "control")
	case "frame_ack":
		return desktopID(command.Generation) && desktopID(command.FrameID)
	case "input":
		return desktopID(command.Generation) && command.Input != nil && command.Input.valid()
	case "unlock_input":
		return desktopID(command.Generation) && desktopID(command.FrameID) && command.Input != nil && command.Input.valid() && command.Input.Kind != "text" && command.Input.Kind != "paste"
	case "unlock_cancel":
		return desktopID(command.Generation)
	case "release_input", "get_clipboard", "lock", "keyframe":
		return desktopID(command.Generation)
	case "service_status":
		return command.Generation == 0
	case "service_install", "service_uninstall", "login_session":
		return command.Generation == 0 && hostDesktopServiceName(command.Service)
	case "set_clipboard":
		return desktopID(command.Generation) && command.Text != nil && hostDesktopText(*command.Text, 1<<20)
	case "set_clipboard_sync":
		return desktopID(command.Generation) && command.Enabled != nil
	}
	return false
}

func hostDesktopAuthorization(value string) bool {
	switch value {
	case "", "unsupported", "needs_consent", "saved", "restoring", "revoked", "unknown":
		return true
	}
	return false
}

func hostDesktopServiceState(value string) bool {
	switch value {
	case "unsupported", "not_installed", "authorization_required", "installing", "active", "stopped", "failed", "uninstalling":
		return true
	}
	return false
}

func (message HostDesktopMessage) valid() bool {
	if !hostDesktopAuthorization(message.Authorization) || message.Capabilities != nil && !hostDesktopAuthorization(message.Capabilities.Authorization) {
		return false
	}
	if message.Version != HostDesktopProtocolVersion || message.Bytes < 0 || message.Bytes > hostDesktopPayloadLimit {
		return false
	}
	if message.ServiceStatus != nil && (!hostDesktopServiceState(message.ServiceStatus.State) || !hostDesktopServiceName(message.Service)) {
		return false
	}
	switch message.Type {
	case "frame":
		if message.Cursor != "" && message.Cursor != "embedded" && message.Cursor != "separate" {
			return false
		}
		return desktopID(message.Generation) && desktopID(message.FrameID) && message.Width >= 2 && message.Width <= 8192 && message.Height >= 2 && message.Height <= 8192 && message.Width*message.Height <= 16<<20 && message.Bytes > 0 && (message.Codec == "h264" || message.Codec == "png") && message.Timestamp >= 0
	case "cursor":
		return desktopID(message.Generation) && message.Codec == "png" && message.Width >= 1 && message.Width <= 512 && message.Height >= 1 && message.Height <= 512 && message.HotX >= 0 && message.HotX < message.Width && message.HotY >= 0 && message.HotY < message.Height && message.Bytes > 0 && message.Bytes <= 2<<20
	case "audio":
		return desktopID(message.Generation) && message.Codec == "opus" && message.SampleRate == 48000 && message.Channels == 2 && message.Bytes > 0 && message.Bytes <= 64<<10 && message.Timestamp >= 0
	case "result", "error", "state", "capabilities", "displays", "clipboard", "service_status":
		return message.Bytes == 0 && (message.Text == nil || hostDesktopText(*message.Text, 1<<20))
	}
	return false
}

// ReadHostDesktopMessage reads bounded native framing without starting a helper,
// owning a session, retrying input or granting paint acknowledgement.
func ReadHostDesktopMessage(reader io.Reader) (HostDesktopMessage, error) {
	var message HostDesktopMessage
	var prefix [4]byte
	if _, err := io.ReadFull(reader, prefix[:]); err != nil {
		return message, err
	}
	size := binary.BigEndian.Uint32(prefix[:])
	if size == 0 || size > hostDesktopHeaderLimit {
		return message, ErrHostDesktopProtocol
	}
	header := make([]byte, size)
	if _, err := io.ReadFull(reader, header); err != nil {
		return message, err
	}
	if !utf8.Valid(header) || !hostDesktopUniqueJSON(header) || json.Unmarshal(header, &message) != nil || !message.valid() {
		return HostDesktopMessage{}, ErrHostDesktopProtocol
	}
	if message.Bytes != 0 {
		message.Data = make([]byte, message.Bytes)
		if _, err := io.ReadFull(reader, message.Data); err != nil {
			return HostDesktopMessage{}, err
		}
	}
	return message, nil
}

func WriteHostDesktopMessage(writer io.Writer, message HostDesktopMessage) error {
	message.Bytes = len(message.Data)
	if !message.valid() {
		return ErrHostDesktopProtocol
	}
	header, err := json.Marshal(message)
	if err != nil {
		return err
	}
	return writeHostDesktopPacket(writer, header, message.Data)
}

// WriteHostDesktopCommand writes one validated native command. It never retries
// a partial input write; its caller must revoke that connection on an error.
func WriteHostDesktopCommand(writer io.Writer, command HostDesktopCommand) error {
	if !command.Valid() {
		return ErrHostDesktopProtocol
	}
	header, err := json.Marshal(command)
	if err != nil {
		return err
	}
	return writeHostDesktopPacket(writer, header, nil)
}

func writeHostDesktopPacket(writer io.Writer, header, payload []byte) error {
	if len(header) > hostDesktopHeaderLimit {
		return ErrHostDesktopProtocol
	}
	var prefix [4]byte
	binary.BigEndian.PutUint32(prefix[:], uint32(len(header)))
	for _, data := range [][]byte{prefix[:], header, payload} {
		for len(data) != 0 {
			n, err := writer.Write(data)
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

func desktopID(value uint64) bool { return value > 0 && value <= 1<<53-1 }
