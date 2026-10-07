package nativeapps

import (
	"context"
	"errors"
	"runtime"
	"sync"
)

const LoginScreenService = "login-screen"

var (
	ErrServiceUnsupported   = errors.New("login-screen service is unsupported")
	ErrServiceAuthorization = errors.New("administrator authorization is required")
	ErrServiceNotInstalled  = errors.New("login-screen service is not installed")
)

// ServiceStatus is the redacted lifecycle state exposed to product UI.
// Paths, tokens, usernames and credentials never cross this boundary.
type ServiceStatus = HostDesktopServiceStatus

const (
	ServiceUnsupported   = "unsupported"
	ServiceNotInstalled  = "not_installed"
	ServiceAuthorization = "authorization_required"
	ServiceInstalling    = "installing"
	ServiceActive        = "active"
	ServiceFailed        = "failed"
	ServiceUninstalling  = "uninstalling"
)

// LoginServiceManager is the narrow lifecycle boundary used by Runtime. A
// platform adapter may be supplied by the native helper; the default adapter
// safely reports unsupported until that helper has been explicitly installed.
type LoginServiceManager struct {
	mu        sync.Mutex
	status    ServiceStatus
	install   func(context.Context) error
	uninstall func(context.Context) error
}

func NewLoginServiceManager() *LoginServiceManager {
	state := ServiceUnsupported
	if runtime.GOOS == "linux" || runtime.GOOS == "darwin" {
		state = ServiceNotInstalled
	}
	return &LoginServiceManager{status: ServiceStatus{State: state, Backend: runtime.GOOS}}
}

func (m *LoginServiceManager) Status(context.Context) (ServiceStatus, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.status, nil
}

// Install and Uninstall are intentionally explicit calls. They do not run at
// startup and never invoke an elevation helper implicitly.
func (m *LoginServiceManager) Install(ctx context.Context) (ServiceStatus, error) {
	m.mu.Lock()
	if m.status.State == ServiceUnsupported {
		s := m.status
		m.mu.Unlock()
		return s, ErrServiceUnsupported
	}
	if m.status.State == ServiceActive {
		s := m.status
		m.mu.Unlock()
		return s, nil
	}
	m.status.State = ServiceInstalling
	fn := m.install
	m.mu.Unlock()
	if fn == nil {
		m.mu.Lock()
		m.status.State = ServiceAuthorization
		s := m.status
		m.mu.Unlock()
		return s, ErrServiceAuthorization
	}
	err := fn(ctx)
	m.mu.Lock()
	defer m.mu.Unlock()
	if err != nil {
		m.status.State, m.status.Reason = ServiceFailed, "install_failed"
		return m.status, err
	}
	m.status.State, m.status.Reason = ServiceActive, ""
	return m.status, nil
}

func (m *LoginServiceManager) Uninstall(ctx context.Context) (ServiceStatus, error) {
	m.mu.Lock()
	if m.status.State == ServiceUnsupported {
		s := m.status
		m.mu.Unlock()
		return s, ErrServiceUnsupported
	}
	if m.status.State == ServiceNotInstalled {
		s := m.status
		m.mu.Unlock()
		return s, nil
	}
	m.status.State = ServiceUninstalling
	fn := m.uninstall
	m.mu.Unlock()
	if fn == nil {
		m.mu.Lock()
		m.status.State, m.status.Reason = ServiceNotInstalled, ""
		s := m.status
		m.mu.Unlock()
		return s, nil
	}
	err := fn(ctx)
	m.mu.Lock()
	defer m.mu.Unlock()
	if err != nil {
		m.status.State, m.status.Reason = ServiceFailed, "uninstall_failed"
		return m.status, err
	}
	m.status.State, m.status.Reason = ServiceNotInstalled, ""
	return m.status, nil
}

// OpenLoginSession is reserved for an already active service and returns a
// one-time attachment token owned by the caller. Platform implementations
// provide the token over the inherited private descriptor.
func (m *LoginServiceManager) OpenLoginSession(context.Context) (string, ServiceStatus, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.status.State == ServiceUnsupported {
		return "", m.status, ErrServiceUnsupported
	}
	if m.status.State != ServiceActive {
		return "", m.status, ErrServiceNotInstalled
	}
	return "", m.status, errors.New("login-screen attachment is not available")
}
