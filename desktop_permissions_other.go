//go:build !linux && !darwin

package nativeapps

func validateDesktopRuntime(string) error { return ErrUnsupported }
