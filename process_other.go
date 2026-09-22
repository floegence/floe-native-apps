//go:build !linux

package nativeapps

func ObserveProcess(_ int) (ProcessIdentity, error) { return ProcessIdentity{}, ErrUnsupported }
