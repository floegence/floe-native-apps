//go:build linux

package hostdesktop

import (
	"errors"
)

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
	frameWindow                int
	release                    func() error
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
	limit := a.frameWindow
	if limit == 0 {
		limit = HostDesktopDefaultFrameWindow
	}
	if a.state != "active" && a.state != "locked" || len(a.offered) >= limit {
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
