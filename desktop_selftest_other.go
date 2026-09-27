//go:build !linux

package nativeapps

import "context"

func DesktopSelfTest(context.Context, string, string) error  { return ErrUnsupported }
func desktopSelfTest(context.Context, string, Package) error { return ErrUnsupported }

func runDesktopSelfTest(context.Context, string, Package, func(string, []byte) error) error {
	return ErrUnsupported
}
