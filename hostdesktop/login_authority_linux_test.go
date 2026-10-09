//go:build linux

package hostdesktop

import (
	"testing"
	"time"
)

func unlockKey(a *loginAuthority) HostDesktopCommand {
	return HostDesktopCommand{Version: 1, ID: 1, Method: "unlock_input", Generation: a.generation, FrameID: a.painted, Input: &HostDesktopInput{Kind: "key", Code: "KeyA", Pressed: true}}
}
func paintedLoginAuthority(t *testing.T) *loginAuthority {
	t.Helper()
	a := &loginAuthority{}
	if err := a.transition(loginSeatState{session: "login", kind: "greeter"}, "control"); err != nil {
		t.Fatal(err)
	}
	frame, ok := a.offer()
	if !ok {
		t.Fatal("frame unavailable")
	}
	if err := a.acknowledge(a.generation, frame); err != nil {
		t.Fatal(err)
	}
	return a
}
func TestLoginAuthorityLockedFrameBinding(t *testing.T) {
	a := paintedLoginAuthority(t)
	command := unlockKey(a)
	if err := a.input(command); err != nil {
		t.Fatal(err)
	}
	command.Method = "input"
	if a.input(command) == nil {
		t.Fatal("ordinary input admitted on lock screen")
	}
	command.Method = "unlock_input"
	command.FrameID++
	if a.input(command) == nil {
		t.Fatal("unpainted frame admitted")
	}
	command = unlockKey(a)
	command.Generation--
	if a.input(command) == nil {
		t.Fatal("old generation admitted")
	}
	command = unlockKey(a)
	command.Input = &HostDesktopInput{Kind: "paste", Text: "secret"}
	if a.input(command) == nil {
		t.Fatal("password paste admitted")
	}
	a.mode = "view"
	if a.input(unlockKey(a)) == nil {
		t.Fatal("view-only unlock admitted")
	}
}

func TestLoginAuthorityClientTextRequiresCurrentActivePaintAndControl(t *testing.T) {
	a := paintedLoginAuthority(t)
	command := HostDesktopCommand{Version: 1, ID: 1, Method: "input", Generation: a.generation,
		Input: &HostDesktopInput{Kind: "paste", Text: "Unicode fixture"}}
	if a.input(command) == nil {
		t.Fatal("locked text admitted")
	}
	if err := a.transition(loginSeatState{session: "user", kind: "user", uid: 1000}, "control"); err != nil {
		t.Fatal(err)
	}
	command.Generation = a.generation
	if a.input(command) == nil {
		t.Fatal("unpainted text admitted")
	}
	frame, _ := a.offer()
	if err := a.acknowledge(a.generation, frame); err != nil {
		t.Fatal(err)
	}
	if err := a.input(command); err != nil {
		t.Fatal(err)
	}
	a.mode = "view"
	if a.input(command) == nil {
		t.Fatal("view-only text admitted")
	}
	a.mode = "control"
	command.Generation--
	if a.input(command) == nil {
		t.Fatal("retired text admitted")
	}
}
func TestLoginAuthorityUnlockRequiresSuccessorPaint(t *testing.T) {
	a := paintedLoginAuthority(t)
	old := unlockKey(a)
	releases := 0
	a.release = func() error { releases++; return nil }
	if err := a.transition(loginSeatState{session: "user", kind: "user", uid: 1000}, "control"); err != nil {
		t.Fatal(err)
	}
	if releases != 1 || a.painted != 0 {
		t.Fatal("transition retained input or paint authority")
	}
	if a.input(old) == nil {
		t.Fatal("old unlock input reached desktop")
	}
	active := old
	active.Method = "input"
	active.Generation = a.generation
	active.FrameID = 0
	if a.input(active) == nil {
		t.Fatal("unpainted active desktop accepted input")
	}
	frame, _ := a.offer()
	if err := a.acknowledge(a.generation, frame); err != nil {
		t.Fatal(err)
	}
	if err := a.input(active); err != nil {
		t.Fatal(err)
	}
}
func TestLoginAuthorityCancelAndUserSwitch(t *testing.T) {
	a := paintedLoginAuthority(t)
	releases := 0
	a.release = func() error { releases++; return nil }
	if err := a.cancel(a.generation); err != nil {
		t.Fatal(err)
	}
	if a.input(unlockKey(a)) == nil {
		t.Fatal("cancel retained authority")
	}
	if err := a.transition(loginSeatState{session: "different-user", kind: "user", uid: 1001}, "view"); err != nil {
		t.Fatal(err)
	}
	if releases != 2 || a.painted != 0 {
		t.Fatal("user switch retained input")
	}
}
func TestLoginAuthorityAttemptLimitAndFrameBound(t *testing.T) {
	a := paintedLoginAuthority(t)
	now := time.Unix(1, 0)
	a.attempts = &loginUnlockLimiter{now: func() time.Time { return now }}
	command := unlockKey(a)
	command.Input.Code = "Enter"
	for i := 0; i < 5; i++ {
		if err := a.input(command); err != nil {
			t.Fatal(err)
		}
	}
	if a.input(command) == nil {
		t.Fatal("unlock attempts not limited")
	}
	now = now.Add(time.Minute)
	if err := a.input(command); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 4; i++ {
		if _, ok := a.offer(); !ok {
			t.Fatal("premature frame bound")
		}
	}
	if _, ok := a.offer(); ok {
		t.Fatal("unbounded unacknowledged frames")
	}
}

func TestLoginUnlockLimiterSurvivesReconnectAndRepeatFlag(t *testing.T) {
	limiter := &loginUnlockLimiter{}
	for i := 0; i < 5; i++ {
		a := paintedLoginAuthority(t)
		a.attempts = limiter
		command := unlockKey(a)
		command.Input.Code = "Enter"
		command.Input.Repeat = true
		if err := a.input(command); err != nil {
			t.Fatal(err)
		}
	}
	successor := paintedLoginAuthority(t)
	successor.attempts = limiter
	command := unlockKey(successor)
	command.Input.Code = "NumpadEnter"
	if successor.input(command) == nil {
		t.Fatal("reconnect reset attempt limiter")
	}
	command.Input = &HostDesktopInput{Kind: "down", Button: 0}
	if successor.input(command) == nil {
		t.Fatal("pointer bypassed unlock interaction limit")
	}
}
