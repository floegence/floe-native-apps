//go:build linux

package hostdesktop

import (
	"context"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"golang.org/x/sys/unix"
)

// Only the qualified GNOME locker may use logind's lock hint as input authority.
// A GDM PAM label alone also admits arbitrary user-selected desktops. Require a
// single live, root-owned GNOME shell image for the active seat's user, and bind
// its process lifetime into the generation. Ambiguous sessions fail closed.
func loginCompositor(ctx context.Context, uid uint32) (string, error) {
	entries, err := os.ReadDir("/proc")
	if err != nil {
		return "", errLoginCaptureUnavailable
	}
	var identity string
	for _, entry := range entries {
		if ctx.Err() != nil {
			return "", ctx.Err()
		}
		number, err := strconv.ParseUint(entry.Name(), 10, 32)
		if err != nil || number == 0 {
			continue
		}
		base := filepath.Join("/proc", entry.Name())
		comm, err := os.ReadFile(filepath.Join(base, "comm"))
		if err != nil || strings.TrimSpace(string(comm)) != "gnome-shell" {
			continue
		}
		var stat unix.Stat_t
		if unix.Stat(base, &stat) != nil || stat.Uid != uid {
			continue
		}
		executable, err := os.Readlink(filepath.Join(base, "exe"))
		if err != nil || executable != "/usr/bin/gnome-shell" || loginRootOwnedExecutable(executable) != nil {
			return "", errLoginCaptureUnavailable
		}
		start, err := loginProcessStart(uint32(number))
		if err != nil || identity != "" {
			return "", errLoginCaptureUnavailable
		}
		identity = entry.Name() + ":" + start
	}
	if identity == "" {
		return "", errLoginCaptureUnavailable
	}
	return identity, nil
}
