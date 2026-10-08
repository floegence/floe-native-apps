package nativeapps

import (
	"context"
	"github.com/floegence/floe-native-apps/hostdesktop"
)

const LoginServiceSocket = hostdesktop.LoginServiceSocket

type LoginServiceConfig = hostdesktop.LoginServiceConfig
type LoginScreenConnection = hostdesktop.LoginScreenConnection

func RunLoginScreenService(ctx context.Context, config LoginServiceConfig) error {
	return hostdesktop.RunLoginScreenService(ctx, config)
}
func InstalledLoginServiceStatus(ctx context.Context, path string) (ServiceStatus, error) {
	return hostdesktop.InstalledLoginServiceStatus(ctx, path)
}
func OpenLoginScreenSession(ctx context.Context, path string) (*LoginScreenConnection, error) {
	return hostdesktop.OpenLoginScreenSession(ctx, path)
}

type LoginServiceDeploymentRequest = hostdesktop.LoginServiceDeploymentRequest
type LoginServiceDeploymentEvent = hostdesktop.LoginServiceDeploymentEvent

func ManageLoginScreenService(ctx context.Context, request LoginServiceDeploymentRequest, report func(LoginServiceDeploymentEvent)) (ServiceStatus, error) {
	return hostdesktop.ManageLoginScreenService(ctx, request, report)
}
