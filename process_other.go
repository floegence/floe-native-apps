//go:build !linux

package nativeapps

func (m *Manager) InstallationForProcess(process ProcessIdentity) (*Installation, error) {
	return nil, ErrUnsupported
}

func ObserveProcess(_ int) (ProcessIdentity, error) { return ProcessIdentity{}, ErrUnsupported }
