//go:build linux || darwin

package nativeapps

import (
	"os"
	"syscall"
)

func validateDesktopRuntime(path string) error {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || int(stat.Uid) != os.Getuid() || info.Mode() != os.ModeDir|0700 {
		return ErrInvalid
	}
	return nil
}
