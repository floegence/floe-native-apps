//go:build linux

package hostdesktop

import (
	"bufio"
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/sys/unix"
)

// logind may publish a successor before the kernel switches its VT.
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
		return errors.New("login-screen service requires root and unprivileged Runtime")
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
	if loginRootOwnedExecutable(config.WorkerPath) != nil || loginMediaTrusted(config.MediaRoot) != nil {
		return errLoginPeerRejected
	}
	var info unix.Stat_t
	if unix.Lstat(filepath.Dir(config.SocketPath), &info) != nil || info.Uid != 0 || info.Gid != config.RuntimeGID || info.Mode&unix.S_IFMT != unix.S_IFDIR || info.Mode&0022 != 0 {
		return errLoginPeerRejected
	}
	if config.SocketPath != filepath.Clean(config.SocketPath) || !filepath.IsAbs(config.SocketPath) {
		return errLoginPeerRejected
	}
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
	defer s.claim(conn, "view")
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
		_ = loginWritePacket(conn, loginServiceReply{Version: loginAttachmentVersion, Status: &ServiceStatus{State: ServiceActive, Backend: "linux-drm-kms"}})
		return
	case "ticket":
		token, err := s.tickets.issue(peer)
		if err == nil {
			_ = loginWritePacket(conn, loginServiceReply{Version: loginAttachmentVersion, Token: token})
		}
		return
	case "attach":
		if hello.Version != loginAttachmentVersion {
			_ = loginWritePacket(conn, loginServiceReply{Version: loginAttachmentVersion, Code: "SERVICE_UPDATE_REQUIRED"})
			return
		}
		if !s.tickets.consume(hello.Token, peer) {
			return
		}
	default:
		return
	}
	media, child, err := loginMediaSocketPair()
	if err != nil {
		return
	}
	defer media.Close()
	defer child.Close()
	if writeLoginAttachment(conn, child) != nil {
		return
	}
	_ = conn.SetDeadline(time.Time{})
	s.attachment(ctx, conn, media, reader, peer)
}

func loginRenderGroups(gid uint32) []uint32 {
	groups := []uint32{gid}
	paths, _ := filepath.Glob("/dev/dri/renderD*")
	for _, path := range paths {
		var stat unix.Stat_t
		if unix.Stat(path, &stat) != nil || stat.Mode&unix.S_IFMT != unix.S_IFCHR {
			continue
		}
		found := false
		for _, group := range groups {
			if group == stat.Gid {
				found = true
			}
		}
		if !found {
			groups = append(groups, stat.Gid)
		}
	}
	return groups
}

func (s *loginServer) attachment(ctx context.Context, conn, media *net.UnixConn, reader *bufio.Reader, peer loginPeerIdentity) {
	lifetime, cancel := context.WithCancel(ctx)
	defer cancel()
	go func() { <-lifetime.Done(); _ = conn.Close(); _ = media.Close() }()
	write := func(message HostDesktopMessage) bool {
		_ = conn.SetWriteDeadline(time.Now().Add(3 * time.Second))
		return WriteHostDesktopMessage(conn, message) == nil
	}
	replyError := func(id uint64, code string) bool {
		return write(HostDesktopMessage{Version: 1, Type: "error", ID: id, Code: code})
	}
	seatReader, err := openLoginSeatReader()
	if err != nil {
		replyError(0, "LOGIN_SESSION_UNSUPPORTED")
		return
	}
	defer seatReader.close()
	readSeat := func() (loginSeatState, error) { return seatReader.seat(lifetime, s.config.Seat) }
	worker, err := openLoginMedia(lifetime, s.config)
	if err != nil {
		replyError(0, loginCaptureOpenCode(err))
		return
	}
	defer worker.close()
	input, err := openLoginUInput()
	if err != nil {
		replyError(0, "INPUT_UNAVAILABLE")
		return
	}
	defer input.close()
	releaseControl := func() { _ = input.release(); s.claim(conn, "view") }
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
	packets := make(chan HostDesktopMessage, 8)
	go writeLoginMedia(lifetime, media, packets, cancel)
	var seed [6]byte
	if _, err = rand.Read(seed[:]); err != nil {
		return
	}
	generation := uint64(0)
	for _, b := range seed {
		generation = generation<<8 | uint64(b)
	}
	authority := loginAuthority{generation: generation, release: input.release, mode: "view", state: "unavailable", attempts: &s.unlockLimiter}
	picture := HostDesktopPicture{Mode: "auto", MaxDimension: 1920, FrameRate: 30}
	transition := func(seat loginSeatState, mode string) bool {
		reset := seat != authority.seat
		if authority.transition(seat, mode) != nil {
			return false
		}
		if reset {
			_ = s.wakeDisplay(lifetime, seat)
		}
		return worker.send(map[string]any{"method": "configure", "generation": authority.generation, "picture": picture, "reset": reset, "settle": reset})
	}
	state := func(id uint64) bool {
		return write(HostDesktopMessage{Version: 1, Type: "state", ID: id, Generation: authority.generation, State: authority.state, Mode: authority.mode, DisplayID: "physical-0"})
	}
	display := HostDesktopDisplay{ID: "physical-0", Name: "Physical display", Scale: 1, Primary: true}
	connected, probing := false, false
	waking := false
	seatTicker := time.NewTicker(100 * time.Millisecond)
	defer seatTicker.Stop()
	for {
		select {
		case <-lifetime.Done():
			return
		case <-worker.done:
			replyError(0, "MEDIA_WORKER_UNAVAILABLE")
			return
		case <-seatReader.bus.Context().Done():
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
				seat, err := readSeat()
				if err != nil {
					if !write(HostDesktopMessage{Version: 1, Type: "capabilities", ID: command.ID, Capabilities: &HostDesktopCapabilities{Backend: "linux-drm-kms", State: "unsupported", Reason: "LOGIN_SESSION_UNSUPPORTED", Displays: []HostDesktopDisplay{}}}) {
						return
					}
					continue
				}
				if probing {
					if !replyError(command.ID, "PROBE_IN_PROGRESS") {
						return
					}
					continue
				}
				// A fixed, nonprivileged display-power operation can wake a connected
				// output. It does not inject input or change the login authority.
				_ = s.wakeDisplay(lifetime, seat)
				probing = true
				if !worker.send(map[string]any{"method": "probe", "id": command.ID}) {
					return
				}
			case "service_status":
				if !write(HostDesktopMessage{Version: 1, Type: "result", ID: command.ID, Service: LoginScreenService, ServiceStatus: &ServiceStatus{State: ServiceActive, Backend: "linux-drm-kms"}}) {
					return
				}
			case "connect", "set_mode", "configure", "keyframe", "select_display":
				if command.DisplayID != "" && command.DisplayID != display.ID {
					if !replyError(command.ID, "DISPLAY_UNSUPPORTED") {
						return
					}
					continue
				}
				mode := authority.mode
				if command.Mode != "" {
					mode = command.Mode
				}
				seat, err := readSeat()
				if err != nil {
					if !replyError(command.ID, "LOGIN_SESSION_UNSUPPORTED") {
						return
					}
					continue
				}
				if !s.claim(conn, mode) {
					if !replyError(command.ID, "INPUT_IN_USE") {
						return
					}
					continue
				}
				if command.Picture != nil {
					picture = *command.Picture
					picture.Audio = false
				}
				if !transition(seat, mode) {
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
				if !worker.send(map[string]any{"method": "ack", "generation": authority.generation, "frame_id": command.FrameID}) {
					return
				}
				if !write(HostDesktopMessage{Version: 1, Type: "result", ID: command.ID, Generation: authority.generation}) {
					return
				}
			case "input", "unlock_input":
				if lifetime.Err() != nil {
					return
				}
				seat, err := readSeat()
				if err != nil {
					_ = input.release()
					replyError(command.ID, "LOGIN_SESSION_UNSUPPORTED")
					return
				}
				if seat != authority.seat {
					if !transition(seat, authority.mode) || !state(0) || !replyError(command.ID, "GENERATION_RETIRED") {
						return
					}
					continue
				}
				if err := authority.input(command); err != nil {
					code := "INPUT_REJECTED"
					if errors.Is(err, errLoginUnlockLimited) {
						code = "UNLOCK_RATE_LIMITED"
					}
					if !replyError(command.ID, code) {
						return
					}
					continue
				}
				if err := input.inputPhysical(command.Input, command.Method == "unlock_input"); err != nil {
					if errors.Is(err, errLoginPhysicalInput) {
						if !replyError(command.ID, "INPUT_REJECTED") {
							return
						}
						continue
					}
					return
				}
				worker.interacted(authority.generation)
				if !write(HostDesktopMessage{Version: 1, Type: "result", ID: command.ID, Generation: authority.generation}) {
					return
				}
			case "release_input":
				if input.release() != nil || !write(HostDesktopMessage{Version: 1, Type: "result", ID: command.ID, Generation: authority.generation}) {
					return
				}
			case "unlock_cancel":
				if authority.state != "locked" {
					if !replyError(command.ID, "INPUT_REJECTED") {
						return
					}
					continue
				}
				if !transition(authority.seat, authority.mode) || !state(0) || !write(HostDesktopMessage{Version: 1, Type: "result", ID: command.ID, Generation: authority.generation}) {
					return
				}
			case "lock":
				if authority.state != "active" || authority.mode != "control" || authority.painted == 0 {
					if !replyError(command.ID, "INPUT_REJECTED") {
						return
					}
					continue
				}
				if seatReader.lock(lifetime, authority.seat.session) != nil {
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
			start, err := loginProcessStart(peer.pid)
			var image unix.Stat_t
			if err != nil || start != peer.start || unix.Stat(loginProcessPath(peer.pid, "exe"), &image) != nil || uint64(image.Dev) != peer.dev || image.Ino != peer.ino {
				return
			}
			seat, err := readSeat()
			if err != nil {
				return
			}
			if seat != authority.seat && (!transition(seat, authority.mode) || !state(0)) {
				return
			}
		case <-seatReader.signals:
			if !connected {
				continue
			}
			seat, err := readSeat()
			if err != nil {
				return
			}
			if seat != authority.seat && (!transition(seat, authority.mode) || !state(0)) {
				return
			}
		case message := <-worker.messages:
			if message.Type == "capabilities" {
				probing = false
				seat, err := readSeat()
				if err != nil {
					replyError(message.ID, "LOGIN_SESSION_UNSUPPORTED")
					return
				}
				cap := message.Capabilities
				cap.Service = ServiceActive
				cap.LockedScreen = cap.Screen
				if cap.Screen && seat.state() == "locked" {
					cap.State = "locked"
				}
				if len(cap.Displays) == 1 {
					display = cap.Displays[0]
				}
				if !write(message) {
					return
				}
				continue
			}
			if message.Type == "error" {
				_ = input.release()
				if message.Code == "DISPLAY_INACTIVE" && connected && message.Generation != authority.generation {
					continue
				}
				if message.Code == "DISPLAY_INACTIVE" && connected && !waking {
					seat, err := readSeat()
					if err != nil || authority.transition(seat, authority.mode) != nil {
						return
					}
					waking = true
					if !state(0) || s.wakeDisplay(lifetime, seat) != nil {
						replyError(0, "DISPLAY_INACTIVE")
						return
					}
					if !worker.send(map[string]any{"method": "configure", "generation": authority.generation, "picture": picture, "reset": true, "settle": true}) {
						return
					}
					continue
				}
				replyError(0, message.Code)
				return
			}
			if !connected || message.Generation != authority.generation {
				continue
			}
			if message.Type == "displays" {
				if len(message.Displays) != 1 || message.Displays[0].ID != display.ID {
					return
				}
				seat, err := readSeat()
				if err != nil {
					return
				}
				display = message.Displays[0]
				if !transition(seat, authority.mode) || !state(0) {
					return
				}
				message.Generation = authority.generation
				if !write(message) {
					return
				}
				continue
			}
			if message.Type != "frame" && message.Type != "cursor" {
				return
			}
			if message.Type == "frame" {
				waking = false
				seat, err := readSeat()
				if err != nil {
					return
				}
				if seat != authority.seat {
					if !transition(seat, authority.mode) || !state(0) {
						return
					}
					continue
				}
				if !authority.acceptFrame(message.Generation, message.FrameID) {
					return
				}
				message.DisplayID = display.ID
			}
			select {
			case packets <- message:
			default:
				return
			}
		}
	}
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
