//go:build !linux

package hostdesktop

import "context"

func ManageLoginScreenServiceControlled(context.Context, LoginServiceDeploymentRequest, func(LoginServiceDeploymentEvent), LoginServiceDeploymentControl) (ServiceStatus, error) {
	return ServiceStatus{State: ServiceUnsupported}, ErrServiceUnsupported
}
