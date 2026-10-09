//go:build !linux

package hostdesktop

import (
	"context"
	"net"
)

func RunLoginMediaPython(_, _ string) error           { return ErrServiceUnsupported }
func RunLoginTextPython(string, string, string) error { return ErrServiceUnsupported }

func RunLoginScreenService(context.Context, LoginServiceConfig) error { return ErrServiceUnsupported }

func loginServerIdentity(net.Conn) error { return ErrServiceUnsupported }

func readLoginAttachment(*net.UnixConn) (*net.UnixConn, error) { return nil, ErrServiceUnsupported }
