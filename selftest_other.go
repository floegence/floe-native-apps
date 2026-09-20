//go:build !linux

package nativeapps

import "context"

// Only Linux executes graphical components. Other hosts can acquire bundles.
func SelfTest(context.Context, string) error { return ErrUnsupported }
