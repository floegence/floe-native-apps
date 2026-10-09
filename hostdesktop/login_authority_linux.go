//go:build linux

package hostdesktop

import (
	"errors"
	"sync"
	"time"
)

var errLoginUnlockLimited = errors.New("unlock interaction rate limited")

var errLoginAuthority = errors.New("desktop input authority rejected")

type loginSeatState struct {
	session, kind, compositor, lockSession string
	uid                                    uint32
	vt                                     uint32
	locked                                 bool
}

func (s loginSeatState) state() string {
	if s.kind == "greeter" || s.locked {
		return "locked"
	}
	if s.kind == "user" {
		return "active"
	}
	return "unavailable"
}

// loginAuthority is owned by one attachment's dispatch loop. Capture workers
// cannot mutate it. Every state, display, mode or media change retires the
// painted-frame receipt and releases held input before publishing a successor.
type loginAuthority struct {
	generation, frame, painted uint64
	mode, state                string
	seat                       loginSeatState
	offered                    map[uint64]bool
	release                    func() error
	attempts                   *loginUnlockLimiter
}

func (a *loginAuthority) transition(seat loginSeatState, mode string) error {
	if a.release != nil {
		if err := a.release(); err != nil {
			return err
		}
	}
	a.generation++
	a.frame = 0
	a.painted = 0
	a.offered = map[uint64]bool{}
	a.seat = seat
	a.state = seat.state()
	a.mode = mode
	return nil
}
func (a *loginAuthority) offer() (uint64, bool) {
	if a.state != "active" && a.state != "locked" || len(a.offered) >= 4 {
		return 0, false
	}
	a.frame++
	a.offered[a.frame] = true
	return a.frame, true
}
func (a *loginAuthority) acknowledge(generation, frame uint64) error {
	if generation != a.generation || !a.offered[frame] || frame < a.painted {
		return errLoginAuthority
	}
	for id := range a.offered {
		if id <= frame {
			delete(a.offered, id)
		}
	}
	a.painted = frame
	return nil
}

func (a *loginAuthority) acceptFrame(generation, frame uint64) bool {
	if generation != a.generation || frame != a.frame+1 {
		return false
	}
	id, ok := a.offer()
	return ok && id == frame
}
func (a *loginAuthority) input(command HostDesktopCommand) error {
	if !command.Valid() || a.mode != "control" || command.Generation != a.generation || a.painted == 0 {
		return errLoginAuthority
	}
	if a.state == "locked" {
		if command.Method != "unlock_input" || command.FrameID != a.painted {
			return errLoginAuthority
		}
		if (command.Input.Kind == "key" && (command.Input.Code == "Enter" || command.Input.Code == "NumpadEnter") && command.Input.Pressed) || (command.Input.Kind == "down" && command.Input.Button == 0) {
			if a.attempts == nil {
				a.attempts = &loginUnlockLimiter{}
			}
			if !a.attempts.admit() {
				return errLoginUnlockLimited
			}
		}
	} else if a.state != "active" || command.Method != "input" {
		return errLoginAuthority
	}
	if a.state == "locked" && (command.Input == nil || command.Input.Text != "" || command.Input.Kind == "text" || command.Input.Kind == "paste") {
		return errLoginPhysicalInput
	}
	return nil
}
func (a *loginAuthority) cancel(generation uint64) error {
	if generation != a.generation || a.state != "locked" {
		return errLoginAuthority
	}
	return a.transition(a.seat, a.mode)
}

// The service shares this limiter across attachments, so reconnecting, canceling
// or starting another viewer cannot reset the administrator-authorized boundary.
type loginUnlockLimiter struct {
	mu       sync.Mutex
	attempts []time.Time
	now      func() time.Time
}

func (l *loginUnlockLimiter) admit() bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.now == nil {
		l.now = time.Now
	}
	now := l.now()
	recent := l.attempts[:0]
	for _, at := range l.attempts {
		if now.Sub(at) < time.Minute {
			recent = append(recent, at)
		}
	}
	l.attempts = recent
	if len(recent) >= 5 {
		return false
	}
	l.attempts = append(l.attempts, now)
	return true
}
