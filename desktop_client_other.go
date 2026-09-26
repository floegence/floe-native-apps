//go:build !linux

package nativeapps

import "context"

// DialDesktop attaches to the Linux graphical helper on its own host. Other
// platforms retain their existing native backends; no remote socket fallback
// or second authentication channel is introduced here.
func DialDesktop(context.Context, DesktopEndpoint) (*DesktopConnection, DesktopState, error) {
	return nil, DesktopState{}, ErrUnsupported
}
