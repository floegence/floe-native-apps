//go:build linux || darwin

package nativeapps

import (
	"os"
	"syscall"
)

func lockRoot(file *os.File) error {
	return syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
}
