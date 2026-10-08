//go:build !linux

package hostdesktop

import "context"

func RunLoginDisplayPower(context.Context) error { return ErrServiceUnsupported }
