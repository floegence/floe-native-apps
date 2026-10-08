//go:build linux

package nativeapps

// The login-screen service is deliberately a small, root-owned process.  It
// has no network listener: the Runtime attaches through the private Unix
// socket created by the installer.  This is the same split used by the
// mature Linux remote-desktop implementations: the user process owns the
// authenticated product session while the service owns KMS and uinput.

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"io"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
	"unsafe"

	"golang.org/x/sys/unix"
)

const (
	LoginServiceSocket = "/run/redeven/login-screen.sock"
	LoginServiceToken  = "/run/redeven/login-screen.token"
)

type LoginServiceConfig struct {
	SocketPath string
	TokenPath  string
	RuntimeUID uint32
	Device     string
	FrameRate  time.Duration
}

type loginServiceRequest struct {
	Method     string            `json:"method"`
	Token      string            `json:"token,omitempty"`
	Generation uint64            `json:"generation,omitempty"`
	FrameID    uint64            `json:"frame_id,omitempty"`
	Input      *HostDesktopInput `json:"input,omitempty"`
}

type loginServiceMessage struct {
	Type       string `json:"type"`
	Code       string `json:"code,omitempty"`
	Generation uint64 `json:"generation,omitempty"`
	FrameID    uint64 `json:"frame_id,omitempty"`
	Width      int    `json:"width,omitempty"`
	Height     int    `json:"height,omitempty"`
	Bytes      int    `json:"bytes,omitempty"`
	Data       string `json:"data,omitempty"`
}

// LoginServiceCapability reports what the privileged process can actually
// operate.  A missing framebuffer or uinput device is a hard unsupported
// result; callers must never fall back to a user-session portal for lockscreen
// access.
func LoginServiceCapability(device string) (HostDesktopServiceStatus, error) {
	if device == "" {
		device = "/dev/fb0"
	}
	if _, err := os.Stat(device); err != nil {
		return HostDesktopServiceStatus{State: ServiceUnsupported, Reason: "drm_framebuffer_unavailable", Backend: "linux-drm-kms"}, nil
	}
	if _, err := os.Stat("/dev/uinput"); err != nil {
		return HostDesktopServiceStatus{State: ServiceUnsupported, Reason: "uinput_unavailable", Backend: "linux-drm-kms"}, nil
	}
	return HostDesktopServiceStatus{State: ServiceActive, Backend: "linux-drm-kms"}, nil
}

func loginServiceToken(path string) ([]byte, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	b = []byte(strings.TrimSpace(string(b)))
	if len(b) < 32 || len(b) > 256 {
		return nil, errors.New("invalid login service token")
	}
	return b, nil
}

func peerUID(conn *net.UnixConn) (uint32, error) {
	var uid uint32
	err := conn.SyscallConn().Control(func(fd uintptr) {
		ucred, e := unix.GetsockoptUcred(int(fd), unix.SOL_SOCKET, unix.SO_PEERCRED)
		if e != nil {
			err = e
			return
		}
		uid = ucred.Uid
	})
	return uid, err
}

func writeLoginMessage(w io.Writer, message loginServiceMessage) error {
	b, err := json.Marshal(message)
	if err != nil {
		return err
	}
	_, err = fmt.Fprintf(w, "%s\n", b)
	return err
}

type loginFramebuffer struct {
	file   *os.File
	width  int
	height int
	stride int
	mu     sync.Mutex
}

func readFBInt(path string, fallback int) int {
	b, err := os.ReadFile(path)
	if err != nil {
		return fallback
	}
	n, err := strconv.Atoi(strings.TrimSpace(string(b)))
	if err != nil || n <= 0 {
		return fallback
	}
	return n
}

func openLoginFramebuffer(device string) (*loginFramebuffer, error) {
	if device == "" {
		device = "/dev/fb0"
	}
	size, err := os.ReadFile("/sys/class/graphics/fb0/virtual_size")
	if err != nil {
		return nil, fmt.Errorf("read DRM/KMS geometry: %w", err)
	}
	parts := strings.Split(strings.TrimSpace(string(size)), ",")
	if len(parts) != 2 {
		return nil, errors.New("invalid DRM/KMS geometry")
	}
	w, _ := strconv.Atoi(parts[0])
	h, _ := strconv.Atoi(parts[1])
	if w < 2 || h < 2 || w > 8192 || h > 8192 {
		return nil, errors.New("unsupported DRM/KMS geometry")
	}
	file, err := os.Open(device)
	if err != nil {
		return nil, err
	}
	stride := readFBInt("/sys/class/graphics/fb0/stride", w*4)
	if stride < w*4 || stride > 64<<10 {
		_ = file.Close()
		return nil, errors.New("unsupported DRM/KMS stride")
	}
	return &loginFramebuffer{file: file, width: w, height: h, stride: stride}, nil
}

func (f *loginFramebuffer) png() ([]byte, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if _, err := f.file.Seek(0, io.SeekStart); err != nil {
		return nil, err
	}
	buf := make([]byte, f.stride*f.height)
	if _, err := io.ReadFull(f.file, buf); err != nil {
		return nil, err
	}
	pixels := image.NewRGBA(image.Rect(0, 0, f.width, f.height))
	for y := 0; y < f.height; y++ {
		row := buf[y*f.stride : y*f.stride+f.width*4]
		for x := 0; x < f.width; x++ {
			i := x * 4
			// DRM XRGB8888 is little-endian B,G,R,X.
			pixels.SetRGBA(x, y, color.RGBA{R: row[i+2], G: row[i+1], B: row[i], A: 255})
		}
	}
	var out bytes.Buffer
	if err := png.Encode(&out, pixels); err != nil {
		return nil, err
	}
	return out.Bytes(), nil
}

type loginUInput struct {
	file    *os.File
	mu      sync.Mutex
	keys    map[int]bool
	buttons map[int]bool
}

func openLoginUInput() (*loginUInput, error) {
	file, err := os.OpenFile("/dev/uinput", os.O_WRONLY|os.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	for _, event := range []int{1, 3, 0} { // EV_KEY, EV_ABS, EV_SYN
		if err := loginIoctlInt(int(file.Fd()), 0x40045564, event); err != nil {
			_ = file.Close()
			return nil, err
		}
	}
	for _, code := range []int{272, 273, 274} {
		_ = loginIoctlInt(int(file.Fd()), 0x40045565, code) // UI_SET_KEYBIT
	}
	_ = loginIoctlInt(int(file.Fd()), 0x40045567, 0) // UI_SET_ABSBIT / ABS_X
	_ = loginIoctlInt(int(file.Fd()), 0x40045567, 1) // ABS_Y
	setup := make([]byte, 92)                        // input_id + name[80] + ff_effects_max
	binary.LittleEndian.PutUint16(setup[0:2], 3)     // BUS_USB
	binary.LittleEndian.PutUint16(setup[2:4], 0x2f6f)
	binary.LittleEndian.PutUint16(setup[4:6], 1)
	binary.LittleEndian.PutUint16(setup[6:8], 1)
	copy(setup[8:], "Redeven login-screen keyboard and pointer")
	if err := loginIoctlBytes(int(file.Fd()), 0x405c5503, setup); err != nil {
		_ = file.Close()
		return nil, err
	}
	if err := loginIoctlInt(int(file.Fd()), 0x5501, 0); err != nil {
		_ = file.Close()
		return nil, err
	}
	return &loginUInput{file: file, keys: map[int]bool{}, buttons: map[int]bool{}}, nil
}

func loginIoctlInt(fd, request, value int) error {
	_, _, errno := unix.Syscall(unix.SYS_IOCTL, uintptr(fd), uintptr(request), uintptr(value))
	if errno != 0 {
		return errno
	}
	return nil
}
func loginIoctlBytes(fd, request int, value []byte) error {
	_, _, errno := unix.Syscall(unix.SYS_IOCTL, uintptr(fd), uintptr(request), uintptr(unsafe.Pointer(&value[0])))
	if errno != 0 {
		return errno
	}
	return nil
}

func (u *loginUInput) close() {
	if u == nil || u.file == nil {
		return
	}
	_ = loginIoctlInt(int(u.file.Fd()), 0x5502, 0)
	_ = u.file.Close()
}

func (u *loginUInput) event(typ, code, value int16) error {
	// input_event is timeval + type/code/value.  The kernel accepts zero time.
	b := make([]byte, 24)
	binary.LittleEndian.PutUint16(b[16:18], uint16(typ))
	binary.LittleEndian.PutUint16(b[18:20], uint16(code))
	binary.LittleEndian.PutUint32(b[20:24], uint32(value))
	_, err := u.file.Write(b)
	return err
}

func (u *loginUInput) input(value *HostDesktopInput, width, height int) error {
	if value == nil || !value.valid() {
		return errors.New("invalid physical input")
	}
	u.mu.Lock()
	defer u.mu.Unlock()
	switch value.Kind {
	case "key":
		code := linuxKeyCode(value.Code)
		if code == 0 {
			return errors.New("unsupported key")
		}
		if err := u.event(unix.EV_KEY, int16(code), boolInt(value.Pressed)); err != nil {
			return err
		}
		u.keys[code] = value.Pressed
	case "move", "down", "up":
		if value.Kind == "move" || value.Kind == "down" || value.Kind == "up" {
			x := int16(clamp(value.X*float64(width), 0, float64(width-1)))
			y := int16(clamp(value.Y*float64(height), 0, float64(height-1)))
			if err := u.event(unix.EV_ABS, unix.ABS_X, x); err != nil {
				return err
			}
			if err := u.event(unix.EV_ABS, unix.ABS_Y, y); err != nil {
				return err
			}
		}
		if value.Kind == "down" || value.Kind == "up" {
			code := []int16{unix.BTN_LEFT, unix.BTN_RIGHT, unix.BTN_MIDDLE}[min(value.Button, 2)]
			if err := u.event(unix.EV_KEY, code, boolInt(value.Kind == "down")); err != nil {
				return err
			}
			u.buttons[int(code)] = value.Kind == "down"
		}
	default:
		return errors.New("unsupported physical input")
	}
	return u.event(unix.EV_SYN, unix.SYN_REPORT, 0)
}

func boolInt(v bool) int16 {
	if v {
		return 1
	}
	return 0
}
func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
func clamp(v, lo, hi float64) float64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

func linuxKeyCode(code string) int {
	if strings.HasPrefix(code, "Key") && len(code) == 4 {
		if c := code[3]; c >= 'A' && c <= 'Z' {
			return int(c-'A') + 30
		}
	}
	if strings.HasPrefix(code, "Digit") && len(code) == 6 {
		if c := code[5]; c >= '1' && c <= '9' {
			return int(c-'1') + 2
		}
		if c == '0' {
			return 11
		}
	}
	return map[string]int{"Enter": 28, "Escape": 1, "Backspace": 14, "Tab": 15, "Space": 57, "ShiftLeft": 42, "ShiftRight": 54, "ControlLeft": 29, "ControlRight": 97, "AltLeft": 56, "AltRight": 100, "ArrowUp": 103, "ArrowDown": 108, "ArrowLeft": 105, "ArrowRight": 106, "Delete": 111, "Home": 102, "End": 107}[code]
}

func RunLoginScreenService(ctx context.Context, cfg LoginServiceConfig) error {
	if cfg.SocketPath == "" {
		cfg.SocketPath = LoginServiceSocket
	}
	if cfg.TokenPath == "" {
		cfg.TokenPath = LoginServiceToken
	}
	if cfg.FrameRate <= 0 {
		cfg.FrameRate = 500 * time.Millisecond
	}
	if os.Geteuid() != 0 {
		return errors.New("login-screen service must run as root")
	}
	token, err := loginServiceToken(cfg.TokenPath)
	if err != nil {
		return err
	}
	capability, _ := LoginServiceCapability(cfg.Device)
	if capability.State != ServiceActive {
		return errors.New(capability.Reason)
	}
	fb, err := openLoginFramebuffer(cfg.Device)
	if err != nil {
		return err
	}
	defer fb.file.Close()
	uinput, err := openLoginUInput()
	if err != nil {
		return fmt.Errorf("open uinput: %w", err)
	}
	defer uinput.close()
	if err := os.MkdirAll(filepath.Dir(cfg.SocketPath), 0750); err != nil {
		return err
	}
	_ = os.Remove(cfg.SocketPath)
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: cfg.SocketPath, Net: "unix"})
	if err != nil {
		return err
	}
	defer listener.Close()
	_ = os.Chmod(cfg.SocketPath, 0660)
	go func() { <-ctx.Done(); _ = listener.Close() }()
	for {
		conn, err := listener.AcceptUnix()
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			continue
		}
		go serveLoginClient(ctx, conn, token, cfg.RuntimeUID, fb, uinput, cfg.FrameRate)
	}
}

func serveLoginClient(ctx context.Context, conn *net.UnixConn, token []byte, uid uint32, fb *loginFramebuffer, input *loginUInput, rate time.Duration) {
	defer conn.Close()
	peer, err := peerUID(conn)
	if err != nil || (uid != 0 && peer != uid) {
		return
	}
	reader := bufio.NewReaderSize(conn, 64<<10)
	line, err := reader.ReadBytes('\n')
	if err != nil {
		return
	}
	var hello loginServiceRequest
	if json.Unmarshal(line, &hello) != nil || hello.Method != "hello" {
		return
	}
	providedHash, expectedHash := sha256.Sum256([]byte(hello.Token)), sha256.Sum256(token)
	if subtle.ConstantTimeCompare(providedHash[:], expectedHash[:]) != 1 {
		return
	}
	if err := writeLoginMessage(conn, loginServiceMessage{Type: "ready", Width: fb.width, Height: fb.height, Generation: hello.Generation}); err != nil {
		return
	}
	var writeMu sync.Mutex
	send := func(m loginServiceMessage) error {
		writeMu.Lock()
		defer writeMu.Unlock()
		return writeLoginMessage(conn, m)
	}
	go func() {
		ticker := time.NewTicker(rate)
		defer ticker.Stop()
		var frame uint64
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				data, err := fb.png()
				if err != nil {
					_ = send(loginServiceMessage{Type: "error", Code: "DRM_FRAME_UNAVAILABLE"})
					continue
				}
				frame++
				_ = send(loginServiceMessage{Type: "frame", Generation: hello.Generation, FrameID: frame, Width: fb.width, Height: fb.height, Bytes: len(data), Data: base64.StdEncoding.EncodeToString(data)})
			}
		}
	}()
	for {
		line, err := reader.ReadBytes('\n')
		if err != nil {
			return
		}
		var req loginServiceRequest
		if json.Unmarshal(line, &req) != nil || req.Generation != hello.Generation {
			return
		}
		switch req.Method {
		case "input":
			if err := input.input(req.Input, fb.width, fb.height); err != nil {
				_ = send(loginServiceMessage{Type: "error", Code: "INPUT_REJECTED"})
				return
			}
		case "cancel":
			return
		default:
			_ = send(loginServiceMessage{Type: "error", Code: "INVALID_ARGUMENT"})
		}
	}
}
