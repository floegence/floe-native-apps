//go:build !linux

package nativeapps

import (
	"context"
	"errors"
)

const (
	LoginServiceSocket = ""
	LoginServiceToken  = ""
)

type LoginServiceConfig struct {
	SocketPath string
	TokenPath  string
	RuntimeUID uint32
}

func LoginServiceCapability(string) (HostDesktopServiceStatus, error) {
	return HostDesktopServiceStatus{State: ServiceUnsupported, Reason: "platform_unsupported"}, nil
}

func RunLoginScreenService(context.Context, LoginServiceConfig) error {
	return errors.New("login-screen service is unsupported on this platform")
}
