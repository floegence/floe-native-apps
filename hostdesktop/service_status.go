package hostdesktop

import "errors"

const LoginScreenService = "login-screen"

type ServiceStatus = HostDesktopServiceStatus

const (
	ServiceUnsupported   = "unsupported"
	ServiceNotInstalled  = "not_installed"
	ServiceAuthorization = "authorization_required"
	ServiceInstalling    = "installing"
	ServiceActive        = "active"
	ServiceStopped       = "stopped"
	ServiceFailed        = "failed"
	ServiceUninstalling  = "uninstalling"
)

var (
	ErrServiceUnsupported    = errors.New("login-screen service is unsupported")
	ErrServiceAuthorization  = errors.New("administrator authorization is required")
	ErrServiceNotInstalled   = errors.New("login-screen service is not installed")
	ErrServiceUpdateRequired = errors.New("desktop service update required")
)
