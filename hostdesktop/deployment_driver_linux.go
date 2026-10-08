//go:build linux

package hostdesktop

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"time"

	"golang.org/x/sys/unix"
)

const loginDriverConfig = "# Managed by Redeven physical desktop service\nuinput\n"

func loginDriverPresent() bool {
	var stat unix.Stat_t
	return unix.Stat("/dev/uinput", &stat) == nil && stat.Mode&unix.S_IFMT == unix.S_IFCHR && unix.Major(uint64(stat.Rdev)) == 10 && unix.Minor(uint64(stat.Rdev)) == 223
}

func loginLoadDriver(ctx context.Context, unload bool) error {
	args := []string{"uinput"}
	if unload {
		args = []string{"-r", "uinput"}
	}
	command := exec.CommandContext(ctx, "/usr/sbin/modprobe", args...)
	command.Env = []string{"PATH=/usr/bin:/bin", "LANG=C"}
	return command.Run()
}

// The SSH management transaction owns the boot loader file. The steady-state
// daemon never receives CAP_SYS_MODULE. A failed fresh load is undone only when
// the kernel can safely remove the module; concurrent clients make rollback fail
// explicitly rather than forcing removal or changing device permissions.
func (d *loginDeployer) prepareDriver(ctx context.Context, operation string) (func(context.Context) error, error) {
	noop := func(context.Context) error { return nil }
	if d.driverFile == "" {
		return noop, nil
	}
	before, err := os.ReadFile(d.driverFile)
	existed := err == nil
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return noop, errLoginDeployment
	}
	if existed && string(before) != loginDriverConfig {
		return noop, errLoginDeployment
	}
	newlyLoaded := false
	undo := func(rollback context.Context) error {
		var result error
		if existed {
			result = loginWriteAtomic(d.driverFile, before, 0644)
		} else {
			if err := os.Remove(d.driverFile); err != nil && !errors.Is(err, os.ErrNotExist) {
				result = err
			}
		}
		if newlyLoaded {
			result = errors.Join(result, d.loadDriver(rollback, true))
		}
		return result
	}
	if operation == "uninstall" {
		if !existed {
			return noop, nil
		}
		if err := os.Remove(d.driverFile); err != nil {
			return noop, err
		}
		return undo, nil
	}
	if d.driverPresent != nil && !d.driverPresent() {
		wasLoaded := false
		if d.driverLoaded != nil {
			wasLoaded = d.driverLoaded()
		}
		loadedNow := func() bool {
			if d.driverLoaded != nil {
				return !wasLoaded && d.driverLoaded()
			}
			return d.driverPresent()
		}
		if err := d.loadDriver(ctx, false); err != nil {
			newlyLoaded = loadedNow()
			return undo, errLoginDeployment
		}
		newlyLoaded = loadedNow()
		deadline := time.NewTimer(2 * time.Second)
		defer deadline.Stop()
		for !d.driverPresent() {
			select {
			case <-ctx.Done():
				return undo, ctx.Err()
			case <-deadline.C:
				return undo, errLoginDeployment
			case <-time.After(20 * time.Millisecond):
			}
		}
	}
	if !existed {
		if err := loginWriteAtomic(d.driverFile, []byte(loginDriverConfig), 0644); err != nil {
			return undo, err
		}
	}
	return undo, ctx.Err()
}
