//go:build !linux && !darwin && !windows

package artifactcache

import "os"

func lockCache(*os.File) error { return ErrInvalid }
