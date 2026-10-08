package nativeapps

import (
	"context"
	"errors"
	"github.com/floegence/floe-native-apps/hostdesktop"
)

const LoginScreenService = hostdesktop.LoginScreenService

var (
	ErrServiceUnsupported   = hostdesktop.ErrServiceUnsupported
	ErrServiceAuthorization = hostdesktop.ErrServiceAuthorization
	ErrServiceNotInstalled  = hostdesktop.ErrServiceNotInstalled
)

type ServiceStatus = HostDesktopServiceStatus

const (
	ServiceUnsupported   = hostdesktop.ServiceUnsupported
	ServiceNotInstalled  = hostdesktop.ServiceNotInstalled
	ServiceAuthorization = hostdesktop.ServiceAuthorization
	ServiceInstalling    = hostdesktop.ServiceInstalling
	ServiceActive        = hostdesktop.ServiceActive
	ServiceStopped       = hostdesktop.ServiceStopped
	ServiceFailed        = hostdesktop.ServiceFailed
	ServiceUninstalling  = hostdesktop.ServiceUninstalling
)

// LoginServiceManager observes installed state. It never simulates installation
// in memory and never elevates Runtime. Administrator-authorized SSH deployment
// uses ManageLoginScreenService in a separate management process.
type LoginServiceManager struct{}

func NewLoginServiceManager() *LoginServiceManager { return &LoginServiceManager{} }
func (*LoginServiceManager) Status(ctx context.Context) (ServiceStatus, error) {
	return InstalledLoginServiceStatus(ctx, LoginServiceSocket)
}
func (m *LoginServiceManager) Install(ctx context.Context) (ServiceStatus, error) {
	status, err := m.Status(ctx)
	if status.State == ServiceUnsupported {
		return status, ErrServiceUnsupported
	}
	if err == nil && status.State == ServiceActive {
		return status, nil
	}
	return ServiceStatus{State: ServiceAuthorization, Reason: "ssh_administrator_authorization_required", Backend: "linux-drm-kms"}, ErrServiceAuthorization
}
func (m *LoginServiceManager) Uninstall(ctx context.Context) (ServiceStatus, error) {
	status, err := m.Status(ctx)
	if status.State == ServiceUnsupported {
		return status, ErrServiceUnsupported
	}
	if status.State == ServiceNotInstalled {
		return status, nil
	}
	if err != nil {
		return status, err
	}
	return ServiceStatus{State: ServiceAuthorization, Reason: "ssh_administrator_authorization_required", Backend: status.Backend}, ErrServiceAuthorization
}

// Deprecated: use OpenLoginScreenSession, which consumes its one-time ticket
// and owns the private attachment lifecycle rather than exposing an unbound token.
func (m *LoginServiceManager) OpenLoginSession(ctx context.Context) (string, ServiceStatus, error) {
	status, err := m.Status(ctx)
	if err != nil {
		return "", status, err
	}
	return "", status, errors.New("use OpenLoginScreenSession for a private service attachment")
}
