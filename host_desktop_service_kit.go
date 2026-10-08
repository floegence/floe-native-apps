package nativeapps

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
)

// HostDesktopServiceKit is the reviewed release's SSH deployment closure. A host
// adapter selects architecture from the SSH host, never from renderer paths or
// URLs. Extracting this kit never requests authority or installs a service.
type HostDesktopServiceKit struct {
	ServiceSHA256 string            `json:"service_sha256"`
	WorkerSHA256  string            `json:"worker_sha256"`
	Files         map[string][]byte `json:"-"`
}

func LoginScreenServiceKit(architecture string) (HostDesktopServiceKit, error) {
	files, err := hostDesktopFiles(architecture)
	if err != nil {
		return HostDesktopServiceKit{}, err
	}
	kit := HostDesktopServiceKit{Files: map[string][]byte{}}
	for _, name := range []string{"floe-host-desktop-service", "desktop-drm", "libdrmtap.LICENSE", "drm-build.json", "service-build.json"} {
		data := files[name]
		if len(data) == 0 {
			return HostDesktopServiceKit{}, ErrInvalid
		}
		kit.Files[name] = data
	}
	digest := func(data []byte) string { value := sha256.Sum256(data); return hex.EncodeToString(value[:]) }
	kit.ServiceSHA256, kit.WorkerSHA256 = digest(kit.Files["floe-host-desktop-service"]), digest(kit.Files["desktop-drm"])
	return kit, nil
}
func (kit HostDesktopServiceKit) Write(ctx context.Context, directory string) error {
	if !filepath.IsAbs(directory) {
		return ErrInvalid
	}
	if err := os.Mkdir(directory, 0700); err != nil {
		return err
	}
	complete := false
	defer func() {
		if !complete {
			_ = os.RemoveAll(directory)
		}
	}()
	for name, data := range kit.Files {
		if err := ctx.Err(); err != nil {
			return err
		}
		if name != filepath.Base(name) {
			return ErrInvalid
		}
		mode := os.FileMode(0600)
		if hostDesktopExecutable(name) {
			mode = 0700
		}
		if err := os.WriteFile(filepath.Join(directory, name), data, mode); err != nil {
			return err
		}
	}
	complete = true
	return nil
}
