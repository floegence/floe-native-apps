//go:build linux

package hostdesktop

import (
	"context"
	"net"
	"testing"
	"time"
)

func TestLoginMediaBackpressureDoesNotBlockAuthority(t *testing.T) {
	conn, child, err := loginMediaSocketPair()
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	defer child.Close()
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	packets := make(chan HostDesktopMessage, 1)
	stopped := make(chan struct{})
	go func() { writeLoginMedia(ctx, conn, packets, cancel); close(stopped) }()
	packets <- HostDesktopMessage{Version: 1, Type: "frame", Generation: 1, FrameID: 1, Codec: "png", Width: 2, Height: 2, Data: make([]byte, 2<<20)}
	// The peer never reads its media FD. Revocation does not need that writer.
	a := loginAuthority{release: func() error { return nil }}
	start := time.Now()
	if err := a.transition(loginSeatState{kind: "user"}, "view"); err != nil {
		t.Fatal(err)
	}
	if time.Since(start) > 10*time.Millisecond {
		t.Fatal("media blocked authority")
	}
	cancel()
	_ = conn.Close()
	select {
	case <-stopped:
	case <-time.After(time.Second):
		t.Fatal("blocked writer survived cancellation")
	}
}

func TestLoginMediaInteractionHintsCoalesceWithoutFillingCommandQueue(t *testing.T) {
	w := &loginMediaWorker{commands: make(chan any, 8), interaction: make(chan uint64, 1)}
	for generation := uint64(1); generation <= 1000; generation++ {
		w.interacted(generation)
	}
	if len(w.commands) != 0 || len(w.interaction) != 1 || <-w.interaction != 1000 {
		t.Fatal("media hints queued input history")
	}
}

func TestLoginMediaFDIsPrivateAndRetiredFramesCannotAuthorize(t *testing.T) {
	control, child, err := loginMediaSocketPair()
	if err != nil {
		t.Fatal(err)
	}
	defer control.Close()
	peer, err := net.FileConn(child)
	_ = child.Close()
	if err != nil {
		t.Fatal(err)
	}
	defer peer.Close()
	media, remote, err := loginMediaSocketPair()
	if err != nil {
		t.Fatal(err)
	}
	defer media.Close()
	defer remote.Close()
	go func() { _ = writeLoginAttachment(control, remote) }()
	received, err := readLoginAttachment(peer.(*net.UnixConn))
	// Success requires a root creator; unprivileged test runners must reject it.
	if err == nil {
		defer received.Close()
	}
	if err != nil && err != errLoginPeerRejected {
		t.Fatal(err)
	}
	a := loginAuthority{}
	if a.transition(loginSeatState{kind: "user"}, "control") != nil {
		t.Fatal("transition")
	}
	if a.acceptFrame(a.generation-1, 1) || a.acceptFrame(a.generation, 2) {
		t.Fatal("foreign or skipped frame accepted")
	}
	if !a.acceptFrame(a.generation, 1) || a.painted != 0 {
		t.Fatal("media granted input")
	}
	if a.acknowledge(a.generation, 1) != nil {
		t.Fatal("current receipt rejected")
	}
	if a.transition(loginSeatState{kind: "user", locked: true}, "control") != nil || a.painted != 0 {
		t.Fatal("lock retained receipt")
	}
}
