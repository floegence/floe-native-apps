//go:build linux

package hostdesktop

import (
	"bufio"
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"image"
	"image/png"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/sys/unix"
)

func loginSeat(ctx context.Context, seat string) (loginSeatState, error) {
	var state loginSeatState
	output, err := exec.CommandContext(ctx, "/usr/bin/loginctl", "show-seat", seat, "-p", "ActiveSession", "--value").Output()
	if err != nil {
		return state, err
	}
	id := strings.TrimSpace(string(output))
	if id == "" || len(id) > 80 || strings.ContainsAny(id, " \t\n/") {
		return state, errLoginCaptureUnavailable
	}
	output, err = exec.CommandContext(ctx, "/usr/bin/loginctl", "show-session", id, "-p", "Class", "-p", "Type", "-p", "LockedHint", "-p", "User", "-p", "Service", "-p", "Active", "-p", "VTNr").Output()
	if err != nil {
		return state, err
	}
	state.session = id
	values := map[string]string{}
	for _, line := range strings.Split(string(output), "\n") {
		key, value, ok := strings.Cut(line, "=")
		if ok {
			values[key] = value
		}
	}
	terminal, err := os.ReadFile("/sys/class/tty/tty0/active")
	if err != nil || !loginVirtualTerminalReady(values["VTNr"], values["Active"], string(terminal)) {
		return state, errLoginCaptureUnavailable
	}
	vt, _ := strconv.ParseUint(values["VTNr"], 10, 32)
	state.vt = uint32(vt)
	if values["Type"] != "wayland" && values["Type"] != "x11" {
		return state, errLoginCaptureUnavailable
	}
	if values["Class"] != "greeter" && values["Class"] != "user" {
		return state, errLoginCaptureUnavailable
	}
	// Only qualified display-manager sessions may assert lock/unlock authority.
	// An arbitrary Wayland locker need not update logind's LockedHint, so treating
	// every compositor as equivalent could admit ordinary input on a lock screen.
	switch values["Service"] {
	case "gdm-password", "gdm-autologin", "gdm-launch-environment":
	default:
		return state, errLoginCaptureUnavailable
	}

	uid, err := strconv.ParseUint(values["User"], 10, 32)
	if err != nil {
		return state, errLoginCaptureUnavailable
	}
	state.compositor, err = loginCompositor(ctx, uint32(uid))
	if err != nil {
		return state, err
	}
	state.kind = values["Class"]
	state.uid = uint32(uid)
	state.locked = values["LockedHint"] == "yes"
	return state, nil
}

// logind can publish the successor before the kernel has switched its VT.
// Until both identify the same active terminal, neither pixels nor input may
// claim that successor's login-screen authority.
func loginVirtualTerminalReady(number, active, current string) bool {
	vt, err := strconv.ParseUint(number, 10, 32)
	return err == nil && vt > 0 && active == "yes" && strings.TrimSpace(current) == "tty"+strconv.FormatUint(vt, 10)
}

type loginServer struct {
	config        LoginServiceConfig
	digest        [32]byte
	tickets       loginTickets
	mu            sync.Mutex
	controller    *net.UnixConn
	unlockLimiter loginUnlockLimiter
}

func RunLoginScreenService(ctx context.Context, config LoginServiceConfig) error {
	if os.Geteuid() != 0 || config.RuntimeUID == 0 {
		return errors.New("login-screen service policy requires root service and unprivileged Runtime")
	}
	if config.SocketPath == "" {
		config.SocketPath = LoginServiceSocket
	}
	if config.Seat == "" {
		config.Seat = "seat0"
	}
	if config.Seat != "seat0" {
		return errors.New("unsupported login-screen seat")
	}
	digest, err := hex.DecodeString(config.RuntimeSHA256)
	if err != nil || len(digest) != 32 {
		return errLoginPeerRejected
	}
	if err = loginRootOwnedExecutable(config.WorkerPath); err != nil {
		return err
	}
	parent := filepath.Dir(config.SocketPath)
	var info unix.Stat_t
	if unix.Lstat(parent, &info) != nil || info.Uid != 0 || info.Gid != config.RuntimeGID || info.Mode&unix.S_IFMT != unix.S_IFDIR || info.Mode&0022 != 0 {
		return errLoginPeerRejected
	}
	if config.SocketPath != filepath.Clean(config.SocketPath) || !filepath.IsAbs(config.SocketPath) {
		return errLoginPeerRejected
	}
	// Refuse unrelated existing paths; systemd's RuntimeDirectory owns cleanup.
	if _, err = os.Lstat(config.SocketPath); !errors.Is(err, os.ErrNotExist) {
		return errors.New("login-screen socket already exists")
	}
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: config.SocketPath, Net: "unix"})
	if err != nil {
		return err
	}
	defer listener.Close()
	if err = os.Chown(config.SocketPath, 0, int(config.RuntimeGID)); err != nil {
		return err
	}
	if err = os.Chmod(config.SocketPath, 0660); err != nil {
		return err
	}
	server := &loginServer{config: config}
	copy(server.digest[:], digest)
	lifetime, cancel := context.WithCancel(ctx)
	defer cancel()
	go func() { <-lifetime.Done(); _ = listener.Close() }()
	var children sync.WaitGroup
	defer func() { cancel(); children.Wait() }()
	admitted := make(chan struct{}, 16)
	for {
		conn, err := listener.AcceptUnix()
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			return err
		}
		select {
		case admitted <- struct{}{}:
		default:
			_ = conn.Close()
			continue
		}
		children.Add(1)
		go func() { defer children.Done(); defer func() { <-admitted }(); server.serve(lifetime, conn) }()
	}
}

func (s *loginServer) claim(conn *net.UnixConn, mode string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.controller == conn {
		s.controller = nil
	}
	if mode == "view" {
		return true
	}
	if s.controller != nil {
		return false
	}
	s.controller = conn
	return true
}
func (s *loginServer) serve(ctx context.Context, conn *net.UnixConn) {
	defer conn.Close()
	defer func() {
		s.mu.Lock()
		if s.controller == conn {
			s.controller = nil
		}
		s.mu.Unlock()
	}()
	_ = conn.SetDeadline(time.Now().Add(5 * time.Second))
	peer, err := loginPeer(conn, s.config.RuntimeUID, s.digest)
	if err != nil {
		return
	}
	reader := bufio.NewReader(conn)
	var hello loginServiceHello
	if loginReadPacket(reader, &hello) != nil {
		return
	}
	switch hello.Operation {
	case "status":
		_ = loginWritePacket(conn, loginServiceReply{Status: &ServiceStatus{State: ServiceActive, Backend: "linux-drm-kms"}})
		return
	case "ticket":
		token, err := s.tickets.issue(peer)
		if err != nil {
			return
		}
		_ = loginWritePacket(conn, loginServiceReply{Token: token})
		return
	case "attach":
		if !s.tickets.consume(hello.Token, peer) {
			return
		}
	default:
		return
	}
	if loginWritePacket(conn, loginServiceReply{}) != nil {
		return
	}
	_ = conn.SetDeadline(time.Time{})
	s.attachment(ctx, conn, reader, peer)
}

func loginRenderGroups(gid uint32) []uint32 {
	groups := []uint32{gid}
	paths, _ := filepath.Glob("/dev/dri/renderD*")
	for _, path := range paths {
		var stat unix.Stat_t
		if unix.Stat(path, &stat) == nil && stat.Mode&unix.S_IFMT == unix.S_IFCHR {
			exists := false
			for _, g := range groups {
				if g == stat.Gid {
					exists = true
				}
			}
			if !exists {
				groups = append(groups, stat.Gid)
			}
		}
	}
	return groups
}

type loginCaptureResult struct {
	image      image.Image
	err        error
	generation uint64
}

func (s *loginServer) attachment(ctx context.Context, conn *net.UnixConn, reader *bufio.Reader, peer loginPeerIdentity) {
	lifetime, cancel := context.WithCancel(ctx)
	defer cancel()
	go func() { <-lifetime.Done(); _ = conn.Close() }()
	capture, err := openLoginDRMCapture(lifetime, s.config.WorkerPath, s.config.RuntimeUID, s.config.RuntimeGID, loginRenderGroups(s.config.RuntimeGID))
	if err != nil {
		_ = WriteHostDesktopMessage(conn, HostDesktopMessage{Version: 1, Type: "error", Code: loginCaptureOpenCode(err)})
		return
	}
	defer capture.close()
	input, err := openLoginUInput()
	if err != nil {
		_ = WriteHostDesktopMessage(conn, HostDesktopMessage{Version: 1, Type: "error", Code: "INPUT_UNAVAILABLE"})
		return
	}
	defer input.close()
	// EOF revokes control before a slow capture worker is reaped. A successor
	// may claim control only after held events have been released.
	releaseControl := func() {
		_ = input.release()
		s.claim(conn, "view")
	}
	defer releaseControl()
	go func() { <-lifetime.Done(); releaseControl() }()
	commands := make(chan HostDesktopCommand, 16)
	go func() {
		defer cancel()
		for {
			command, err := readLoginDesktopCommand(reader)
			if err != nil {
				return
			}
			select {
			case commands <- command:
			case <-lifetime.Done():
				return
			}
		}
	}()
	var seed [6]byte
	if _, err = rand.Read(seed[:]); err != nil {
		return
	}
	generation := uint64(0)
	for _, b := range seed {
		generation = generation<<8 | uint64(b)
	}
	authority := loginAuthority{generation: generation, release: input.release, mode: "view", state: "unavailable", attempts: &s.unlockLimiter}
	transition := func(seat loginSeatState, mode string) error {
		if seat.session != authority.seat.session || seat.vt != authority.seat.vt || seat.uid != authority.seat.uid {
			capture.invalidate()
		}
		return authority.transition(seat, mode)
	}
	write := func(message HostDesktopMessage) bool {
		_ = conn.SetWriteDeadline(time.Now().Add(3 * time.Second))
		return WriteHostDesktopMessage(conn, message) == nil
	}
	replyError := func(id uint64, code string) bool {
		return write(HostDesktopMessage{Version: 1, Type: "error", ID: id, Code: code})
	}
	state := func(id uint64) bool {
		return write(HostDesktopMessage{Version: 1, Type: "state", ID: id, Generation: authority.generation, State: authority.state, Mode: authority.mode, DisplayID: "physical-0"})
	}
	display := HostDesktopDisplay{ID: "physical-0", Name: "Physical display", Scale: 1, Primary: true}
	connected := false
	seatTicker := time.NewTicker(100 * time.Millisecond)
	defer seatTicker.Stop()
	frameTicker := time.NewTicker(time.Second / 15)
	defer frameTicker.Stop()
	captured := make(chan loginCaptureResult, 1)
	busy := false
	for {
		select {
		case <-lifetime.Done():
			return
		case command := <-commands:
			if command.Generation != 0 && command.Generation != authority.generation {
				if !replyError(command.ID, "GENERATION_RETIRED") {
					return
				}
				continue
			}
			switch command.Method {
			case "probe":
				capabilities := &HostDesktopCapabilities{Backend: "linux-drm-kms", State: "ready", Screen: true, Input: true, Unattended: true, Unlock: true, LockedScreen: true, Service: ServiceActive, Encoder: "png", Displays: []HostDesktopDisplay{display}}
				if !connected {
					seat, seatErr := loginSeat(lifetime, s.config.Seat)
					if seatErr != nil {
						capabilities.State = "unsupported"
						capabilities.Reason = "LOGIN_SESSION_UNSUPPORTED"
						capabilities.Screen = false
						capabilities.Input = false
						capabilities.Unlock = false
						capabilities.LockedScreen = false
						capabilities.Displays = nil
						if !write(HostDesktopMessage{Version: 1, Type: "capabilities", ID: command.ID, Capabilities: capabilities}) {
							return
						}
						continue
					}
					if seat.state() == "locked" {
						capabilities.State = "locked"
					}
					pixels, captureErr := s.captureFrame(lifetime, capture)
					if captureErr != nil {
						capabilities.State = "unavailable"
						capabilities.Reason = loginCaptureReason(captureErr)
						capabilities.Screen = false
						capabilities.Input = false
						capabilities.Unlock = false
						capabilities.LockedScreen = false
						capabilities.Displays = nil
					} else {
						display.Width, display.Height = pixels.Bounds().Dx(), pixels.Bounds().Dy()
						capabilities.Displays = []HostDesktopDisplay{display}
					}
				}
				if !write(HostDesktopMessage{Version: 1, Type: "capabilities", ID: command.ID, Capabilities: capabilities}) {
					return
				}
			case "service_status":
				if !write(HostDesktopMessage{Version: 1, Type: "result", ID: command.ID, Service: LoginScreenService, ServiceStatus: &ServiceStatus{State: ServiceActive, Backend: "linux-drm-kms"}}) {
					return
				}
			case "connect", "set_mode", "configure", "keyframe", "select_display":
				if command.DisplayID != "" && command.DisplayID != "physical-0" {
					if !replyError(command.ID, "DISPLAY_UNSUPPORTED") {
						return
					}
					continue
				}
				mode := authority.mode
				if command.Mode != "" {
					mode = command.Mode
				}
				if !s.claim(conn, mode) {
					if !replyError(command.ID, "INPUT_IN_USE") {
						return
					}
					continue
				}
				seat, err := loginSeat(lifetime, s.config.Seat)
				if err != nil {
					if !replyError(command.ID, "LOGIN_SESSION_UNSUPPORTED") {
						return
					}
					continue
				}
				if err = transition(seat, mode); err != nil {
					return
				}
				connected = true
				if !state(command.ID) {
					return
				}
			case "frame_ack":
				if authority.acknowledge(command.Generation, command.FrameID) != nil {
					if !replyError(command.ID, "FRAME_ACK_REJECTED") {
						return
					}
					continue
				}
				if !write(HostDesktopMessage{Version: 1, Type: "result", ID: command.ID, Generation: authority.generation}) {
					return
				}
			case "input", "unlock_input":
				if lifetime.Err() != nil {
					return
				}
				// Check logind again at the input boundary. Polling alone leaves a window
				// where a key could reach the successor user or newly unlocked desktop.
				seat, err := loginSeat(lifetime, s.config.Seat)
				if err != nil || seat != authority.seat {
					_ = input.release()
					if err == nil {
						if transition(seat, authority.mode) != nil {
							return
						}
						if !state(0) {
							return
						}
					}
					if !replyError(command.ID, "GENERATION_RETIRED") {
						return
					}
					continue
				}
				if authorityErr := authority.input(command); authorityErr != nil {
					code := "INPUT_REJECTED"
					if errors.Is(authorityErr, errLoginUnlockLimited) {
						code = "UNLOCK_RATE_LIMITED"
					}
					if !replyError(command.ID, code) {
						return
					}
					continue
				}
				if inputErr := input.inputPhysical(command.Input, command.Method == "unlock_input"); inputErr != nil {
					if errors.Is(inputErr, errLoginPhysicalInput) {
						if !replyError(command.ID, "INPUT_REJECTED") {
							return
						}
						continue
					}
					return
				}
				if !write(HostDesktopMessage{Version: 1, Type: "result", ID: command.ID, Generation: authority.generation}) {
					return
				}
			case "release_input":
				if input.release() != nil {
					return
				}
				if !write(HostDesktopMessage{Version: 1, Type: "result", ID: command.ID, Generation: authority.generation}) {
					return
				}
			case "unlock_cancel":
				if authority.cancel(command.Generation) != nil {
					if !replyError(command.ID, "INPUT_REJECTED") {
						return
					}
					continue
				}
				if !state(0) {
					return
				}
				if !write(HostDesktopMessage{Version: 1, Type: "result", ID: command.ID, Generation: authority.generation}) {
					return
				}
			case "lock":
				if authority.state != "active" || authority.mode != "control" || authority.painted == 0 {
					if !replyError(command.ID, "INPUT_REJECTED") {
						return
					}
					continue
				}
				if exec.CommandContext(lifetime, "/usr/bin/loginctl", "lock-session", authority.seat.session).Run() != nil {
					if !replyError(command.ID, "LOCK_UNAVAILABLE") {
						return
					}
					continue
				}
				if !write(HostDesktopMessage{Version: 1, Type: "result", ID: command.ID, Generation: authority.generation}) {
					return
				}
			case "disconnect":
				return
			default:
				if !replyError(command.ID, "OPERATION_UNSUPPORTED") {
					return
				}
			}
		case <-seatTicker.C:
			if !connected {
				continue
			}
			// A Runtime exec/restart or seat change retires the old input immediately.
			start, err := loginProcessStart(peer.pid)
			var imageStat unix.Stat_t
			imageErr := unix.Stat(loginProcessPath(peer.pid, "exe"), &imageStat)
			if err != nil || start != peer.start || imageErr != nil || uint64(imageStat.Dev) != peer.dev || imageStat.Ino != peer.ino {
				return
			}
			seat, err := loginSeat(lifetime, s.config.Seat)
			if err != nil {
				_ = input.release()
				return
			}
			if seat != authority.seat {
				if transition(seat, authority.mode) != nil {
					return
				}
				if !state(0) {
					return
				}
			}
		case <-frameTicker.C:
			if !connected || busy || len(authority.offered) >= 4 {
				continue
			}
			busy = true
			captureGeneration := authority.generation
			go func() {
				pixels, err := s.captureFrame(lifetime, capture)
				select {
				case captured <- loginCaptureResult{pixels, err, captureGeneration}:
				case <-lifetime.Done():
				}
			}()
		case result := <-captured:
			busy = false
			if result.generation != authority.generation {
				continue
			}
			if result.err != nil {
				if !replyError(0, loginCaptureReason(result.err)) {
					return
				}
				return
			}
			// Capture can straddle a lock or user transition. Discard it and acquire
			// another frame rather than relabeling old pixels with a new generation.
			seat, err := loginSeat(lifetime, s.config.Seat)
			if err != nil {
				return
			}
			if seat != authority.seat {
				if transition(seat, authority.mode) != nil {
					return
				}
				if !state(0) {
					return
				}
				continue
			}
			bounds := result.image.Bounds()
			if display.Width != bounds.Dx() || display.Height != bounds.Dy() {
				display.Width, display.Height = bounds.Dx(), bounds.Dy()
				if transition(seat, authority.mode) != nil {
					return
				}
				if !state(0) {
					return
				}
				if !write(HostDesktopMessage{Version: 1, Type: "displays", Displays: []HostDesktopDisplay{display}, Generation: authority.generation}) {
					return
				}
			}
			frame, ok := authority.offer()
			if !ok {
				continue
			}
			var encoded bytes.Buffer
			if (&png.Encoder{CompressionLevel: png.BestSpeed}).Encode(&encoded, result.image) != nil {
				return
			}
			if !write(HostDesktopMessage{Version: 1, Type: "frame", Generation: authority.generation, FrameID: frame, DisplayID: display.ID, Width: display.Width, Height: display.Height, Codec: "png", Key: true, Data: encoded.Bytes()}) {
				return
			}
		}
	}
}

func loginCaptureReason(err error) string {
	if errors.Is(err, errLoginDisplayInactive) {
		paths, _ := filepath.Glob("/sys/class/drm/card*-*/status")
		for _, path := range paths {
			data, _ := os.ReadFile(path)
			if strings.TrimSpace(string(data)) == "connected" {
				return "DISPLAY_INACTIVE"
			}
		}
		return "DISPLAY_DISCONNECTED"
	}
	if errors.Is(err, errLoginGPUUnsupported) {
		return "GPU_SCANOUT_UNSUPPORTED"
	}
	if errors.Is(err, errLoginDRMPermission) {
		return "DRM_PERMISSION_UNAVAILABLE"
	}
	return "DRM_CAPTURE_UNAVAILABLE"
}

func loginCaptureOpenCode(err error) string {
	if errors.Is(err, errLoginConverterStart) {
		return "DRM_CONVERTER_START_UNAVAILABLE"
	}
	if errors.Is(err, errLoginExporterStart) {
		return "DRM_EXPORTER_START_UNAVAILABLE"
	}
	if errors.Is(err, errLoginPeerRejected) {
		return "DRM_WORKER_IDENTITY_REJECTED"
	}
	if errors.Is(err, unix.EACCES) || errors.Is(err, unix.EPERM) {
		return "DRM_WORKER_PERMISSION_UNAVAILABLE"
	}
	if errors.Is(err, unix.ENOENT) {
		return "DRM_WORKER_RUNTIME_UNAVAILABLE"
	}
	return "DRM_CAPTURE_UNAVAILABLE"
}
