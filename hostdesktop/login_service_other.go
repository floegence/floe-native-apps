//go:build !linux

package hostdesktop

import (
	"context"
	"net"
)

func RunLoginScreenService(context.Context, LoginServiceConfig) error { return ErrServiceUnsupported }

func loginServerIdentity(net.Conn) error { return ErrServiceUnsupported }
