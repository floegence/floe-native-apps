package hostdesktop

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"strings"
	"testing"
)

func TestHostDesktopFramingKeepsControlAndMediaBoundaries(t *testing.T) {
	var stream bytes.Buffer
	frame := HostDesktopMessage{Version: 1, Type: "frame", Generation: 2, FrameID: 3, Codec: "h264", Width: 1920, Height: 1080, Key: true, Cursor: "separate", Data: []byte{0, 0, 0, 1, 0x65}}
	if err := WriteHostDesktopMessage(&stream, frame); err != nil {
		t.Fatal(err)
	}
	if err := WriteHostDesktopMessage(&stream, HostDesktopMessage{Version: 1, Type: "result", ID: 10}); err != nil {
		t.Fatal(err)
	}
	got, err := ReadHostDesktopMessage(&stream)
	if err != nil || !bytes.Equal(got.Data, frame.Data) || got.FrameID != 3 || got.Cursor != "separate" {
		t.Fatalf("frame boundary: %#v, %v", got, err)
	}
	got, err = ReadHostDesktopMessage(&stream)
	if err != nil || got.Type != "result" || got.ID != 10 || len(got.Data) != 0 {
		t.Fatalf("control boundary: %#v, %v", got, err)
	}
}

func TestHostDesktopRejectsOversizedAndTruncatedNativeMessages(t *testing.T) {
	for _, size := range []uint32{0, hostDesktopHeaderLimit + 1, 0xffffffff} {
		var header [4]byte
		binary.BigEndian.PutUint32(header[:], size)
		if _, err := ReadHostDesktopMessage(bytes.NewReader(header[:])); !errors.Is(err, ErrHostDesktopProtocol) {
			t.Errorf("header %d: %v", size, err)
		}
	}
	var stream bytes.Buffer
	_ = WriteHostDesktopMessage(&stream, HostDesktopMessage{Version: 1, Type: "frame", Generation: 1, FrameID: 1, Width: 2, Height: 2, Codec: "png", Data: []byte("pixels")})
	data := stream.Bytes()
	if _, err := ReadHostDesktopMessage(bytes.NewReader(data[:len(data)-1])); !errors.Is(err, io.ErrUnexpectedEOF) {
		t.Fatalf("truncated payload: %v", err)
	}
}

func TestHostDesktopCommandsNeverAcceptUnboundInputOrArbitrarySources(t *testing.T) {
	for _, raw := range []string{
		`{"version":1,"id":1,"method":"input","input":{"kind":"move","x":0.2,"y":0.4}}`,
		`{"version":1,"id":1,"method":"select_display","display_id":"../../other"}`,
		`{"version":1,"id":1,"method":"connect","display_id":"primary","mode":"admin"}`,
		`{"version":1,"id":1,"method":"input","generation":1,"input":{"kind":"move","x":1.1,"y":0}}`,
		`{"version":2,"id":1,"method":"probe"}`,
		`{"version":1,"id":1,"method":"probe","pid":123}`,
		`{"version":1,"id":1,"method":"probe"} {}`,
		`{"version":1,"id":1,"method":"probe","method":"disconnect"}`,
		`{"version":1,"id":1,"method":"input","generation":1,"input":{"kind":"key","kind":"text","text":"x"}}`,
	} {
		if _, err := ParseHostDesktopCommand([]byte(raw)); err == nil {
			t.Errorf("accepted unsafe command: %s", raw)
		}
	}
	valid := HostDesktopCommand{Version: 1, ID: 1, Method: "input", Generation: 2, Input: &HostDesktopInput{Kind: "move", X: 0.25, Y: 0.5}}
	raw, _ := json.Marshal(valid)
	if _, err := ParseHostDesktopCommand(raw); err != nil {
		t.Fatalf("current normalized pointer: %v", err)
	}
}

func TestHostDesktopUnlockInputRequiresPhysicalEventAndLockedFrame(t *testing.T) {
	for _, input := range []*HostDesktopInput{
		{Kind: "text", Text: "secret"},
		{Kind: "paste", Text: "secret"},
	} {
		command := HostDesktopCommand{Version: 1, ID: 1, Method: "unlock_input", Generation: 2, FrameID: 8, Input: input}
		if command.Valid() {
			t.Fatalf("unlock input accepted non-physical event: %#v", input.Kind)
		}
	}
	valid := HostDesktopCommand{Version: 1, ID: 1, Method: "unlock_input", Generation: 2, FrameID: 8,
		Input: &HostDesktopInput{Kind: "key", Code: "Enter", Key: "Enter", Pressed: true}}
	if !valid.Valid() {
		t.Fatal("physical unlock input rejected")
	}
}

func TestHostDesktopServiceCommandsAreExplicitAndBounded(t *testing.T) {
	for _, method := range []string{"service_status", "service_install", "service_uninstall", "login_session"} {
		command := HostDesktopCommand{Version: 1, ID: 1, Method: method, Service: LoginScreenService}
		if !command.Valid() {
			t.Fatalf("service command rejected: %s", method)
		}
	}
	if (HostDesktopCommand{Version: 1, ID: 1, Method: "service_install", Service: "other"}).Valid() {
		t.Fatal("unknown service accepted")
	}
}

func TestHostDesktopClipboardEscapingFitsWireLimit(t *testing.T) {
	value := strings.Repeat("\x01", 1<<20)
	var wire bytes.Buffer
	if err := WriteHostDesktopMessage(&wire, HostDesktopMessage{Version: 1, Type: "clipboard", Text: &value}); err != nil {
		t.Fatal(err)
	}
	message, err := ReadHostDesktopMessage(&wire)
	if err != nil || message.Text == nil || *message.Text != value {
		t.Fatalf("valid clipboard did not round trip: %v", err)
	}
	data, _ := json.Marshal(HostDesktopCommand{Version: 1, ID: 1, Method: "set_clipboard", Generation: 1, Text: &value})
	if _, err := ParseHostDesktopCommand(data); err != nil {
		t.Fatalf("escaped clipboard command: %v", err)
	}
}

func TestHostDesktopZeroCoordinatesSurviveNativeForwarding(t *testing.T) {
	var wire bytes.Buffer
	command := HostDesktopCommand{Version: 1, ID: 1, Method: "input", Generation: 1, Input: &HostDesktopInput{Kind: "scroll", DY: -40}}
	if err := WriteHostDesktopCommand(&wire, command); err != nil {
		t.Fatal(err)
	}
	data := wire.Bytes()
	if int(binary.BigEndian.Uint32(data[:4])) != len(data)-4 {
		t.Fatal("invalid command frame")
	}
	for _, required := range []string{`"x":0`, `"y":0`, `"dx":0`} {
		if !bytes.Contains(data[4:], []byte(required)) {
			t.Fatalf("native input lost the zero coordinate %s", required)
		}
	}
}

func TestHostDesktopAuthorizationMetadataAndExplicitForget(t *testing.T) {
	for _, state := range []string{"unsupported", "needs_consent", "saved", "restoring", "revoked", "unknown"} {
		message := HostDesktopMessage{Version: 1, Type: "state", State: "authorizing", Authorization: state}
		var wire bytes.Buffer
		if err := WriteHostDesktopMessage(&wire, message); err != nil {
			t.Fatal(err)
		}
		got, err := ReadHostDesktopMessage(&wire)
		if err != nil || got.Authorization != state {
			t.Fatal(got, err)
		}
	}
	if (HostDesktopMessage{Version: 1, Type: "state", Authorization: "token-body"}).valid() {
		t.Fatal("unknown authorization state accepted")
	}
	if _, err := ParseHostDesktopCommand([]byte(`{"version":1,"id":1,"method":"forget_authorization"}`)); err != nil {
		t.Fatal(err)
	}
	if _, err := ParseHostDesktopCommand([]byte(`{"version":1,"id":1,"method":"forget_authorization","generation":1}`)); err == nil {
		t.Fatal("live-session forget accepted")
	}
}

func TestHostDesktopRejectsUnknownCursorPresentation(t *testing.T) {
	frame := HostDesktopMessage{Version: 1, Type: "frame", Generation: 1, FrameID: 1, Codec: "png", Width: 2, Height: 2, Data: []byte("pixels"), Cursor: "guess"}
	if err := WriteHostDesktopMessage(&bytes.Buffer{}, frame); !errors.Is(err, ErrHostDesktopProtocol) {
		t.Fatalf("accepted unknown cursor presentation: %v", err)
	}
}
