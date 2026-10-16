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

type LoginServiceDeploymentCommand = hostdesktop.LoginServiceDeploymentCommand
type LoginServiceDeploymentControl = hostdesktop.LoginServiceDeploymentControl

const LoginServiceManagementVersion = hostdesktop.LoginServiceManagementVersion

type LoginServiceAdmissionProof = hostdesktop.LoginServiceAdmissionProof

func VerifyLoginServiceAdmission(ctx context.Context, path string, proof LoginServiceAdmissionProof) (string, error) {
	return hostdesktop.VerifyLoginServiceAdmission(ctx, path, proof)
}
func ManageLoginScreenServiceControlled(ctx context.Context, request LoginServiceDeploymentRequest, report func(LoginServiceDeploymentEvent), control LoginServiceDeploymentControl) (ServiceStatus, error) {
	return hostdesktop.ManageLoginScreenServiceControlled(ctx, request, report, control)
}
