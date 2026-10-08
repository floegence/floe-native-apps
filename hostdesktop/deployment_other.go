//go:build !linux

package hostdesktop

import "context"

func ManageLoginScreenService(context.Context, LoginServiceDeploymentRequest, func(LoginServiceDeploymentEvent)) (ServiceStatus, error) {
	return ServiceStatus{State: ServiceUnsupported, Reason: "platform_unsupported"}, ErrServiceUnsupported
}
