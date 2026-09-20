//go:build !linux && !darwin

package nativeapps

import "os"

// Offline bundle acquisition is portable; native installation requires POSIX.
func lockRoot(*os.File) error { return ErrUnsupported }
