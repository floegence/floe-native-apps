package nativeapps

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"image"
	"image/color"
	"image/jpeg"
	"image/png"
	"io"
	"net"
	"strings"
	"sync"
	"testing"
	"time"
)

func desktopTestPacket(kind byte, body []byte) []byte {
	packet := make([]byte, 5, 5+len(body))
	packet[0] = kind
	binary.BigEndian.PutUint32(packet[1:], uint32(len(body)))
	return append(packet, body...)
}

func desktopTestJSON(value any) []byte {
	body, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	return desktopTestPacket(1, body)
}

func desktopTestConnection(t *testing.T) (*DesktopConnection, net.Conn) {
	t.Helper()
	client, peer := net.Pipe()
	t.Cleanup(func() { _ = client.Close(); _ = peer.Close() })
	return &DesktopConnection{conn: client, connection: 7, writeTurn: make(chan struct{}, 1)}, peer
}

func desktopTestPNG(t *testing.T) []byte {
	t.Helper()
	im := image.NewRGBA(image.Rect(0, 0, 3, 2))
	for y := range 2 {
		for x := range 3 {
			im.Set(x, y, color.RGBA{R: byte(x * 100), G: byte(y * 200), A: 255})
		}
	}
	var data bytes.Buffer
	if err := png.Encode(&data, im); err != nil {
		t.Fatal(err)
	}
	return data.Bytes()
}

func desktopTestFrame(size int) DesktopEvent {
	return DesktopEvent{Event: "frame", Bytes: size, Frame: &DesktopFrame{
		Encoding: "png", Width: 3, Height: 2, Sequence: 42, Connection: 7, Window: 12, Generation: 81,
	}}
}

func TestDesktopClientClipboardSelection(t *testing.T) {
	for _, name := range []string{"text", "empty", "unavailable", "wrong_connection", "both", "missing", "oversized", "nul", "wrong_event"} {
		t.Run(name, func(t *testing.T) {
			client, peer := desktopTestConnection(t)
			text := "你好 e\u0301 👨‍👩‍👧‍👦"
			selection := &DesktopClipboard{Connection: 7, Window: 1, Generation: 2, Text: &text}
			event := DesktopEvent{Event: "clipboard", Clipboard: selection}
			switch name {
			case "empty":
				text = ""
			case "unavailable":
				selection.Text = nil
				selection.Error = "CLIPBOARD_UNAVAILABLE"
			case "wrong_connection":
				selection.Connection = 6
			case "both":
				selection.Error = "CLIPBOARD_UNAVAILABLE"
			case "missing":
				selection.Text = nil
			case "oversized":
				text = strings.Repeat("x", 16001)
			case "nul":
				text = "\x00"
			case "wrong_event":
				event.Event = "capture_unavailable"
				event.Code = "CAPTURE_UNAVAILABLE"
			}
			go func() { _, _ = peer.Write(desktopTestJSON(event)) }()
			ctx, cancel := context.WithTimeout(t.Context(), time.Second)
			defer cancel()
			got, err := client.Read(ctx)
			if name == "text" || name == "empty" || name == "unavailable" {
				if err != nil || got.Clipboard == nil || got.Clipboard.Error != selection.Error ||
					selection.Text != nil && (got.Clipboard.Text == nil || *got.Clipboard.Text != text) {
					t.Fatalf("selection framing changed: %v", err)
				}
			} else if !errors.Is(err, ErrDesktopProtocol) {
				t.Fatalf("invalid selection accepted: %v", err)
			}
		})
	}
}

func TestDesktopClientFrameAndReplyRemainDistinct(t *testing.T) {
	client, peer := desktopTestConnection(t)
	pixels := desktopTestPNG(t)
	written := make(chan error, 1)
	go func() {
		data := append(desktopTestJSON(desktopTestFrame(len(pixels))), desktopTestPacket(2, pixels)...)
		data = append(data, desktopTestJSON(map[string]any{"id": 1, "result": "submitted"})...)
		// Fragment every header and body, as the actual stream is allowed to do.
		for _, value := range data {
			if _, err := peer.Write([]byte{value}); err != nil {
				written <- err
				return
			}
		}
		written <- nil
	}()
	ctx, cancel := context.WithTimeout(t.Context(), 2*time.Second)
	defer cancel()
	frame, err := client.Read(ctx)
	if err != nil || frame.Frame == nil || frame.Frame.Sequence != 42 || !bytes.Equal(frame.Pixels, pixels) {
		t.Fatalf("frame identity or bytes changed: %v", err)
	}
	decoded, err := png.Decode(bytes.NewReader(frame.Pixels))
	if err != nil || decoded.Bounds().Dx() != 3 || decoded.At(2, 1) != (color.RGBA{R: 200, G: 200, A: 255}) {
		t.Fatalf("received pixels do not match fixture: %v", err)
	}
	result, err := client.Read(ctx)
	if err != nil || result.ID != 1 || string(result.Result) != `"submitted"` || result.Pixels != nil {
		t.Fatalf("response was confused with image data: %#v %v", result, err)
	}
	if err := <-written; err != nil {
		t.Fatal(err)
	}
	if client.sequence != 0 {
		t.Fatal("reading pixels implicitly acknowledged a frame")
	}
}

func TestDesktopClientRejectsInvalidFramesBeforeExposingPixels(t *testing.T) {
	pixels := desktopTestPNG(t)
	for _, name := range []string{"excessive_message", "excessive_frame", "oversized_dimensions", "wrong_connection", "wrong_encoding", "short_payload", "wrong_size", "wrong_geometry", "wrong_kind"} {
		t.Run(name, func(t *testing.T) {
			client, peer := desktopTestConnection(t)
			event := desktopTestFrame(len(pixels))
			payload := bytes.Clone(pixels)
			kind := byte(2)
			switch name {
			case "excessive_frame":
				event.Bytes = desktopFrameLimit + 1
			case "oversized_dimensions":
				event.Frame.Width = 4097
			case "wrong_connection":
				event.Frame.Connection = 6
			case "wrong_encoding":
				event.Frame.Encoding = "raw"
			case "short_payload":
				payload = payload[:2]
			case "wrong_size":
				event.Bytes++
			case "wrong_geometry":
				binary.BigEndian.PutUint32(payload[16:20], 4)
			case "wrong_kind":
				kind = 1
			}
			data := append(desktopTestJSON(event), desktopTestPacket(kind, payload)...)
			if name == "excessive_message" {
				data = []byte{1, 0, 0, 0, 0}
				binary.BigEndian.PutUint32(data[1:], desktopMessageLimit+1)
			}
			done := make(chan struct{})
			go func() {
				defer close(done)
				_, _ = peer.Write(data)
			}()
			ctx, cancel := context.WithTimeout(t.Context(), time.Second)
			defer cancel()
			value, err := client.Read(ctx)
			if !errors.Is(err, ErrDesktopProtocol) || len(value.Pixels) != 0 {
				t.Fatalf("invalid frame was not rejected: %v", err)
			}
			<-done // Invalid framing must close the connection and release the writer.
		})
	}
}

func TestDesktopClientCancellationRevokesPartialRead(t *testing.T) {
	client, peer := desktopTestConnection(t)
	ctx, cancel := context.WithCancel(t.Context())
	done := make(chan struct{})
	go func() {
		defer close(done)
		_, _ = peer.Write([]byte{1, 0}) // Partial packet cannot be reused after cancellation.
		cancel()
	}()
	if _, err := client.Read(ctx); !errors.Is(err, context.Canceled) {
		t.Fatalf("partial read did not preserve cancellation: %v", err)
	}
	<-done
	if _, err := client.Send(t.Context(), DesktopRequest{Method: "status"}); err == nil {
		t.Fatal("cancelled stream remained writable")
	}
}

func TestDesktopClientConcurrentRequestsHaveOneWireSequence(t *testing.T) {
	client, peer := desktopTestConnection(t)
	ctx, cancel := context.WithTimeout(t.Context(), 2*time.Second)
	defer cancel()
	observed := make(chan error, 1)
	go func() {
		for id := uint64(1); id <= 32; id++ {
			body, err := readDesktopPacket(peer, 1, desktopRequestLimit)
			if err != nil {
				observed <- err
				return
			}
			var request struct {
				ID     uint64 `json:"id"`
				Method string `json:"method"`
			}
			if json.Unmarshal(body, &request) != nil || request.ID != id || request.Method != "status" {
				observed <- ErrDesktopProtocol
				return
			}
		}
		observed <- nil
	}()
	var writers sync.WaitGroup
	for range 32 {
		writers.Go(func() {
			if _, err := client.Send(ctx, DesktopRequest{Method: "status"}); err != nil {
				t.Errorf("send request: %v", err)
			}
		})
	}
	writers.Wait()
	if err := <-observed; err != nil {
		t.Fatalf("requests interleaved or lost sequence: %v", err)
	}
}

func TestDesktopClientInvalidTargetAndOversizedInputSendNothing(t *testing.T) {
	client, _ := desktopTestConnection(t)
	for _, request := range []DesktopRequest{
		{Method: "input", Connection: 6, Window: 12, Generation: 81, Operation: json.RawMessage(`{"kind":"text","text":"a"}`)},
		{Method: "input", Connection: 7, Window: 12, Generation: 81, Operation: json.RawMessage(`{"kind":"text","text":"` + string(bytes.Repeat([]byte{'a'}, desktopRequestLimit)) + `"}`)},
		{Method: "frame_ack"},
		{Method: "close_window", Window: desktopMaxID + 1},
		{Method: "status", Window: 12},
		{Method: "terminate"},
		{Method: "terminate_application", Connection: 7},
		{Method: "terminate_application", Window: 12},
		{Method: "terminate_application", Generation: 81},
		{Method: "terminate_application", Frame: 1},
		{Method: "terminate_application", Operation: json.RawMessage(`{"pid":123}`)},
		{Method: "release_input", Connection: 6, Window: 12, Generation: 81},
		{Method: "release_input", Connection: 7, Generation: 81},
		{Method: "release_input", Connection: 7, Window: 12},
		{Method: "release_input", Connection: 7, Window: 12, Generation: 81, Operation: json.RawMessage(`{}`)},
	} {
		if _, err := client.Send(t.Context(), request); !errors.Is(err, ErrInvalid) {
			t.Fatalf("invalid request accepted: %s: %v", request.Method, err)
		}
	}
	if client.sequence != 0 {
		t.Fatal("invalid requests entered the wire sequence")
	}
}

func TestDesktopClientExplicitTerminationHasNoCallerSelectedTarget(t *testing.T) {
	client, peer := desktopTestConnection(t)
	received := make(chan map[string]any, 1)
	go func() {
		body, err := readDesktopPacket(peer, 1, desktopRequestLimit)
		var value map[string]any
		if err != nil || json.Unmarshal(body, &value) != nil {
			received <- nil
			return
		}
		received <- value
	}()
	if _, err := client.Send(t.Context(), DesktopRequest{Method: "terminate_application"}); err != nil {
		t.Fatal(err)
	}
	value := <-received
	if len(value) != 2 || value["method"] != "terminate_application" || value["id"] != float64(1) {
		t.Fatalf("unexpected force-quit request: %v", value)
	}
}

func TestDesktopClientWriteCancellationDoesNotReplay(t *testing.T) {
	client, peer := desktopTestConnection(t)
	ctx, cancel := context.WithCancel(t.Context())
	go func() {
		var fragment [2]byte
		_, _ = io.ReadFull(peer, fragment[:])
		cancel()
	}()
	if _, err := client.Send(ctx, DesktopRequest{Method: "status"}); !errors.Is(err, context.Canceled) {
		t.Fatalf("blocked write did not cancel: %v", err)
	}
	if _, err := client.Send(t.Context(), DesktopRequest{Method: "refresh"}); err == nil {
		t.Fatal("partially written request left a usable stream")
	}
}

func TestDesktopClientQueuedWriteCancellationPreservesActiveRequest(t *testing.T) {
	client, peer := desktopTestConnection(t)
	first := make(chan error, 1)
	go func() {
		id, err := client.Send(t.Context(), DesktopRequest{Method: "status"})
		if err == nil && id != 1 {
			err = ErrDesktopProtocol
		}
		first <- err
	}()
	// Leave the first writer blocked halfway through its packet header.
	var prefix [2]byte
	if _, err := io.ReadFull(peer, prefix[:]); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(t.Context(), 20*time.Millisecond)
	defer cancel()
	queued := make(chan error, 1)
	go func() {
		_, err := client.Send(ctx, DesktopRequest{Method: "refresh"})
		queued <- err
	}()
	select {
	case err := <-queued:
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatalf("queued writer did not honor cancellation: %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("cancelled request remained blocked behind another writer")
	}
	body, err := readDesktopPacket(io.MultiReader(bytes.NewReader(prefix[:]), peer), 1, desktopRequestLimit)
	if err != nil || !bytes.Contains(body, []byte(`"method":"status"`)) {
		t.Fatalf("queued cancellation damaged the active request: %s %v", body, err)
	}
	if err := <-first; err != nil {
		t.Fatal(err)
	}
	go func() {
		id, err := client.Send(t.Context(), DesktopRequest{Method: "refresh"})
		if err == nil && id != 2 {
			err = ErrDesktopProtocol
		}
		first <- err
	}()
	if _, err := readDesktopPacket(peer, 1, desktopRequestLimit); err != nil {
		t.Fatal(err)
	}
	if err := <-first; err != nil {
		t.Fatalf("cancelled queued write consumed a sequence or left the stream unusable: %v", err)
	}
}

func TestDesktopClientRequiresVersionedAuthenticationReply(t *testing.T) {
	for _, name := range []string{"accepted", "stream_v2", "stream_future", "missing_version", "future_version", "unsolicited_state"} {
		t.Run(name, func(t *testing.T) {
			connection, peer := desktopTestConnection(t)
			endpoint := DesktopEndpoint{Instance: "private-instance", Token: string(bytes.Repeat([]byte{'a'}, 64))}
			result := make(chan error, 1)
			go func() {
				body, err := readDesktopPacket(peer, 1, desktopRequestLimit)
				if err != nil {
					result <- err
					return
				}
				var auth map[string]any
				if json.Unmarshal(body, &auth) != nil || len(auth) != 3 || auth["version"] != float64(1) ||
					auth["instance"] != endpoint.Instance || auth["token"] != endpoint.Token {
					result <- ErrDesktopProtocol
					return
				}
				reply := DesktopEvent{Event: "attached", Version: 1, Connection: 7, State: &DesktopState{State: "waiting"}}
				switch name {
				case "stream_v2":
					reply.StreamVersion = 2
				case "stream_future":
					reply.StreamVersion = 3
				case "missing_version":
					reply.Version = 0
				case "future_version":
					reply.Version = 2
				case "unsolicited_state":
					reply.Event = "state"
				}
				_, err = peer.Write(desktopTestJSON(reply))
				result <- err
			}()
			client, state, err := authenticateDesktop(t.Context(), connection.conn, endpoint)
			if name == "accepted" || name == "stream_v2" {
				if err != nil || client.Connection() != 7 || state.State != "waiting" {
					t.Fatalf("valid handshake failed: %v", err)
				}
				if (name == "stream_v2") != (client.StreamVersion() == 2) {
					t.Fatal("lost helper stream capability")
				}
			} else if !errors.Is(err, ErrDesktopProtocol) || client != nil {
				t.Fatalf("invalid handshake accepted: %v", err)
			}
			if err := <-result; err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestDesktopClientCursorPreservesAlphaAndLogicalGeometry(t *testing.T) {
	client, peer := desktopTestConnection(t)
	im := image.NewNRGBA(image.Rect(0, 0, 48, 32))
	im.SetNRGBA(47, 31, color.NRGBA{R: 200, G: 100, B: 50, A: 128})
	var encoded bytes.Buffer
	if err := png.Encode(&encoded, im); err != nil {
		t.Fatal(err)
	}
	pixels := encoded.Bytes()
	cursor := DesktopCursor{Mode: "image", Sequence: 8, Connection: 7, Window: 12, Generation: 81, Encoding: "png", Width: 48, Height: 32, LogicalWidth: 24, LogicalHeight: 16, XHot: 23, YHot: 15}
	event := DesktopEvent{Event: "cursor", Cursor: &cursor, Bytes: len(pixels)}
	go func() { _, _ = peer.Write(append(desktopTestJSON(event), desktopTestPacket(3, pixels)...)) }()
	result, err := client.Read(t.Context())
	if err != nil || result.Cursor == nil || *result.Cursor != cursor || !bytes.Equal(result.Pixels, pixels) {
		t.Fatalf("cursor changed: %#v %v", result, err)
	}
	decoded, err := png.Decode(bytes.NewReader(result.Pixels))
	if err != nil || color.NRGBAModel.Convert(decoded.At(47, 31)) != (color.NRGBA{R: 200, G: 100, B: 50, A: 128}) {
		t.Fatalf("cursor alpha changed: %v", err)
	}
	if client.sequence != 0 {
		t.Fatal("cursor implicitly acknowledged an application frame")
	}
}

func TestDesktopClientCursorRejectsMalformedImages(t *testing.T) {
	for _, name := range []string{"bounds", "hotspot", "connection", "size", "mixed_frame", "hidden_bytes", "wrong_kind"} {
		t.Run(name, func(t *testing.T) {
			client, peer := desktopTestConnection(t)
			pixels := desktopTestPNG(t)
			event := DesktopEvent{Event: "cursor", Bytes: len(pixels), Cursor: &DesktopCursor{Mode: "image", Sequence: 1, Connection: 7, Window: 1, Generation: 1, Encoding: "png", Width: 3, Height: 2, LogicalWidth: 3, LogicalHeight: 2}}
			kind := byte(3)
			switch name {
			case "bounds":
				event.Cursor.Width = 1025
			case "hotspot":
				event.Cursor.XHot = 3
			case "connection":
				event.Cursor.Connection = 6
			case "size":
				event.Bytes = desktopCursorLimit + 1
			case "mixed_frame":
				event.Frame = &DesktopFrame{}
			case "hidden_bytes":
				event.Cursor.Mode = "hidden"
			case "wrong_kind":
				kind = 2
			}
			go func() { _, _ = peer.Write(append(desktopTestJSON(event), desktopTestPacket(kind, pixels)...)) }()
			result, err := client.Read(t.Context())
			if !errors.Is(err, ErrDesktopProtocol) || len(result.Pixels) != 0 {
				t.Fatalf("invalid cursor admitted: %v", err)
			}
		})
	}
}

func TestDesktopClientDamageFrames(t *testing.T) {
	for _, encoding := range []string{"png", "jpeg"} {
		for _, invalid := range []string{"", "outside", "no_base", "future_base", "wrong_size"} {
			t.Run(encoding+"/"+invalid, func(t *testing.T) {
				pixels := desktopTestPNG(t)
				if encoding == "jpeg" {
					var data bytes.Buffer
					if err := jpeg.Encode(&data, image.NewRGBA(image.Rect(0, 0, 3, 2)), &jpeg.Options{Quality: 80}); err != nil {
						t.Fatal(err)
					}
					pixels = data.Bytes()
				}
				event := desktopTestFrame(len(pixels))
				f := event.Frame
				f.Encoding = encoding
				f.Width = 100
				f.Height = 80
				f.X = 12
				f.Y = 14
				f.RegionWidth = 3
				f.RegionHeight = 2
				f.Base = 41
				switch invalid {
				case "outside":
					f.X = 99
				case "no_base":
					f.Base = 0
				case "future_base":
					f.Base = 42
				case "wrong_size":
					f.RegionWidth = 4
				}
				client, peer := desktopTestConnection(t)
				go func() { _, _ = peer.Write(append(desktopTestJSON(event), desktopTestPacket(2, pixels)...)) }()
				ctx, cancel := context.WithTimeout(t.Context(), time.Second)
				defer cancel()
				got, err := client.Read(ctx)
				if invalid != "" {
					if !errors.Is(err, ErrDesktopProtocol) {
						t.Fatalf("invalid region accepted: %v", err)
					}
					return
				}
				if err != nil || got.Frame.X != 12 || got.Frame.Base != 41 || !bytes.Equal(got.Pixels, pixels) {
					t.Fatalf("region lost: %v", err)
				}
			})
		}
	}
}

func TestDesktopClientStreamConfiguration(t *testing.T) {
	for _, mode := range []string{"auto", "clarity", "smooth", "data"} {
		if !(DesktopRequest{Method: "configure_stream", Mode: mode}).valid(7) {
			t.Fatal("valid mode rejected", mode)
		}
	}
	for _, request := range []DesktopRequest{{Method: "configure_stream", Mode: "unknown"}, {Method: "configure_stream", Mode: "auto", Window: 1}, {Method: "refresh", Mode: "auto"}} {
		if request.valid(7) {
			t.Fatal("invalid stream request accepted")
		}
	}
}

func TestDesktopClientAcceptsSmallLosslessWebPAndRejectsDimensions(t *testing.T) {
	pixels, err := base64.StdEncoding.DecodeString("UklGRhwAAABXRUJQVlA4TA8AAAAvAUAAAAcQ/Y/+ByKi/wEA")
	if err != nil {
		t.Fatal(err)
	}
	for _, width := range []int{2, 3} {
		client, peer := desktopTestConnection(t)
		frame := DesktopFrame{Encoding: "webp", Width: width, Height: 2, Sequence: 1, Connection: 7, Window: 12, Generation: 81}
		event := DesktopEvent{Event: "frame", Frame: &frame, Bytes: len(pixels)}
		go func() { _, _ = peer.Write(append(desktopTestJSON(event), desktopTestPacket(2, pixels)...)) }()
		result, err := client.Read(t.Context())
		if width == 2 {
			if err != nil || !bytes.Equal(result.Pixels, pixels) {
				t.Fatal("valid small WebP rejected", err)
			}
		} else if !errors.Is(err, ErrDesktopProtocol) {
			t.Fatal("wrong WebP dimensions accepted", err)
		}
	}
}
