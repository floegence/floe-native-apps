//go:build linux

package hostdesktop

import (
	"errors"
	"testing"
)

func TestLoginCaptureOpenCodePreservesSessionDiagnostics(t *testing.T) {
	cases := []struct {
		name string
		err  error
		want string
	}{
		{name: "stale runtime", err: errors.Join(errLoginSessionStale, errors.New("inode changed")), want: "LOGIN_SESSION_STALE_RUNTIME"},
		{name: "compositor changed", err: errLoginCompositorChanged, want: "LOGIN_SESSION_COMPOSITOR_CHANGED"},
		{name: "compositor missing", err: errLoginCompositorMissing, want: "LOGIN_SESSION_COMPOSITOR_MISSING"},
		{name: "compositor ambiguous", err: errLoginCompositorAmbiguous, want: "LOGIN_SESSION_COMPOSITOR_AMBIGUOUS"},
		{name: "no active seat", err: errLoginNoActiveSeat, want: "LOGIN_SESSION_NO_ACTIVE_SEAT"},
		{name: "tty mismatch", err: errLoginTTYMismatch, want: "LOGIN_SESSION_TTY_MISMATCH"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := loginCaptureOpenCode(tc.err); got != tc.want {
				t.Fatalf("loginCaptureOpenCode() = %q, want %q", got, tc.want)
			}
		})
	}
}
