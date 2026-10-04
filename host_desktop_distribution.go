package nativeapps

import (
	"context"
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"time"
)

const hostDesktopContract = "host-desktop-media-v1"

//go:embed host_desktop_catalog.json host_desktop_releases.json host_desktop_contract.py host_desktop_wire.py host_desktop_identity.py host_desktop_input.py host_desktop_portal.py host_desktop_x11.py host_desktop_xcapture.py host_desktop_media.py host_desktop_nvenc.py host_desktop_helper.py host_desktop_selfcheck.py native/host-desktop/dist native/host-desktop/vendor
var hostDesktopDistribution embed.FS

type hostDesktopRelease struct {
	Version      string                 `json:"version"`
	ID           string                 `json:"id"`
	Digest       string                 `json:"digest"`
	Architecture string                 `json:"architecture"`
	Files        map[string]desktopFile `json:"files"`
}

func hostDesktopReleases() ([]hostDesktopRelease, error) {
	data, err := hostDesktopDistribution.ReadFile("host_desktop_releases.json")
	if err != nil {
		return nil, err
	}
	var releases []hostDesktopRelease
	err = json.Unmarshal(data, &releases)
	return releases, err
}

// HostDesktopForPlatform returns the current-user desktop media closure. It does
// not grant desktop access or select an application-private graphical session.
func HostDesktopForPlatform(platform, architecture string) (Package, error) {
	if platform != "linux" || architecture != "amd64" && architecture != "arm64" {
		return Package{}, ErrUnsupported
	}
	data, err := hostDesktopDistribution.ReadFile("host_desktop_catalog.json")
	if err != nil {
		return Package{}, err
	}
	var packages []Package
	if err := json.Unmarshal(data, &packages); err != nil {
		return Package{}, err
	}
	for _, pkg := range packages {
		if pkg.Architecture == architecture {
			digest, err := hostDesktopSourceDigest(architecture)
			if err != nil {
				return Package{}, err
			}
			pkg.Preparation = &Preparation{Contract: hostDesktopContract, NativeSHA256: digest}
			return pkg, pkg.Validate()
		}
	}
	return Package{}, ErrUnsupported
}

func hostDesktopFiles(architecture string) (map[string][]byte, error) {
	if architecture != "amd64" && architecture != "arm64" {
		return nil, ErrUnsupported
	}
	files := map[string][]byte{}
	entries, err := hostDesktopDistribution.ReadDir(".")
	if err != nil {
		return nil, err
	}
	for _, entry := range entries {
		if filepath.Ext(entry.Name()) != ".py" {
			continue
		}
		data, err := hostDesktopDistribution.ReadFile(entry.Name())
		if err != nil {
			return nil, err
		}
		files[entry.Name()] = data
	}
	// Own native binaries, provenance and permissively licensed API headers are
	// bound into the same preparation digest as the helper. Host driver libraries
	// are never downloaded or copied into the isolated media runtime.
	for name, path := range map[string]string{
		"desktop-nvenc":    "native/host-desktop/dist/" + architecture + "/desktop-nvenc",
		"nvenc-build.json": "native/host-desktop/dist/" + architecture + "/manifest.json",
		"nvEncodeAPI.h":    "native/host-desktop/vendor/nvEncodeAPI.h",
		"dynlink_cuda.h":   "native/host-desktop/vendor/dynlink_cuda.h",
	} {
		data, err := hostDesktopDistribution.ReadFile(path)
		if err != nil {
			return nil, err
		}
		files[name] = data
	}
	loader := map[string]string{"amd64": "x86_64", "arm64": "aarch64"}[architecture]
	files["python3"] = []byte("#!/bin/sh\nROOT=$(CDPATH= cd -- \"$(dirname -- \"$0\")/../..\" && pwd) || exit 1\n" +
		"export PYTHONHOME=\"$ROOT/usr\" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1\n" +
		"export GI_TYPELIB_PATH=\"$ROOT/usr/lib/girepository-1.0\" GIO_MODULE_DIR=\"$ROOT/usr/lib/gio/modules\"\n" +
		"export GST_PLUGIN_SYSTEM_PATH_1_0=\"$ROOT/usr/lib/gstreamer-1.0\" GST_REGISTRY=/dev/null GST_REGISTRY_FORK=no\n" +
		"export PIPEWIRE_MODULE_DIR=\"$ROOT/usr/lib/pipewire-0.3\" SPA_PLUGIN_DIR=\"$ROOT/usr/lib/spa-0.2\" PIPEWIRE_CONFIG_DIR=\"$ROOT/usr/share/pipewire\"\n" +
		"unset PYTHONPATH GIO_EXTRA_MODULES GTK_PATH LD_PRELOAD LD_LIBRARY_PATH GST_PLUGIN_PATH GST_PLUGIN_PATH_1_0 GST_PLUGIN_SYSTEM_PATH\n" +
		"exec \"$ROOT/lib/ld-musl-" + loader + ".so.1\" --library-path \"$ROOT/lib:$ROOT/usr/lib:$ROOT/usr/lib/pipewire-0.3:$ROOT/usr/lib/pulseaudio:$ROOT/usr/lib/libproxy\" \"$ROOT/usr/bin/python3\" \"$@\"\n")
	return files, nil
}

func hostDesktopSourceDigest(architecture string) (string, error) {
	files, err := hostDesktopFiles(architecture)
	if err != nil {
		return "", err
	}
	data, err := json.Marshal(files) // Stable sorted keys bind helper and wrapper bytes.
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:]), nil
}

// HostDesktopTools holds only the verified helper paths. The consumer passes a
// private persistent --state directory, --media-fd, and framed commands on stdin;
// stdout is control only. The media descriptor is a separate inherited pipe.
type HostDesktopTools struct{ Root, Python, Helper string }

func ResolveHostDesktopTools(root, architecture string) (HostDesktopTools, error) {
	files, err := hostDesktopFiles(architecture)
	if err != nil {
		return HostDesktopTools{}, err
	}
	manifest := make(map[string]desktopFile, len(files))
	for name, data := range files {
		digest := sha256.Sum256(data)
		manifest[name] = desktopFile{Size: int64(len(data)), SHA256: hex.EncodeToString(digest[:]), Executable: name == "python3" || name == "desktop-nvenc"}
	}
	return resolveHostDesktopFiles(root, architecture, manifest)
}

func resolveHostDesktopInstallation(root string, item Installation) (HostDesktopTools, error) {
	current, err := HostDesktopForPlatform("linux", item.Architecture)
	if err != nil {
		return HostDesktopTools{}, err
	}
	if item.Digest == current.Digest() {
		return ResolveHostDesktopTools(root, item.Architecture)
	}
	releases, err := hostDesktopReleases()
	if err != nil {
		return HostDesktopTools{}, err
	}
	for _, release := range releases {
		if release.Digest == item.Digest && release.Architecture == item.Architecture {
			return resolveHostDesktopFiles(root, item.Architecture, release.Files)
		}
	}
	return HostDesktopTools{}, ErrUnsupported
}

func resolveHostDesktopFiles(root, architecture string, manifest map[string]desktopFile) (HostDesktopTools, error) {
	if !filepath.IsAbs(root) {
		return HostDesktopTools{}, ErrInvalid
	}
	var err error
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return HostDesktopTools{}, err
	}
	loader := map[string]string{"amd64": "x86_64", "arm64": "aarch64"}[architecture]
	for _, name := range []string{"usr/bin/python3", "lib/ld-musl-" + loader + ".so.1", "usr/lib/gstreamer-1.0/libgstpipewire.so", "usr/lib/gstreamer-1.0/libgstx264.so", "usr/lib/gstreamer-1.0/libgstopus.so"} {
		if _, err := containedDesktopFile(root, name); err != nil {
			return HostDesktopTools{}, err
		}
	}
	base := filepath.Join(root, "floe", "host-desktop")
	for name, expected := range manifest {
		path, err := containedDesktopFile(root, filepath.Join("floe", "host-desktop", name))
		if err != nil {
			return HostDesktopTools{}, err
		}
		if err := verifyDesktopFile(path, expected); err != nil {
			return HostDesktopTools{}, err
		}
	}
	return HostDesktopTools{Root: root, Python: filepath.Join(base, "python3"), Helper: filepath.Join(base, "host_desktop_helper.py")}, nil
}

func prepareHostDesktopTools(ctx context.Context, root, architecture string) error {
	files, err := hostDesktopFiles(architecture)
	if err != nil {
		return err
	}
	base := filepath.Join(root, "floe", "host-desktop")
	if err := os.MkdirAll(filepath.Dir(base), 0700); err != nil {
		return err
	}
	if err := os.Mkdir(base, 0700); err != nil {
		return err
	}
	complete := false
	defer func() {
		if !complete {
			_ = os.RemoveAll(base)
		}
	}()
	for name, data := range files {
		if err := ctx.Err(); err != nil {
			return err
		}
		mode := os.FileMode(0600)
		if name == "python3" || name == "desktop-nvenc" {
			mode = 0700
		}
		if err := os.WriteFile(filepath.Join(base, name), data, mode); err != nil {
			return err
		}
	}
	if _, err := ResolveHostDesktopTools(root, architecture); err != nil {
		return err
	}
	complete = true
	return nil
}

// HostDesktopSelfTest proves the installed codec round trip using synthetic
// pixels and audio. Interactive current-desktop authorization and qualification
// remain separate and are never requested as a side effect of installation.
func HostDesktopSelfTest(parent context.Context, root, architecture string) error {
	if runtime.GOOS != "linux" || runtime.GOARCH != architecture {
		return ErrUnsupported
	}
	tools, err := ResolveHostDesktopTools(root, architecture)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(parent, 45*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, tools.Python, filepath.Join(filepath.Dir(tools.Helper), "host_desktop_selfcheck.py"))
	if output, err := command.CombinedOutput(); err != nil {
		if len(output) > 4096 {
			output = output[len(output)-4096:]
		}
		return fmt.Errorf("host desktop media check: %w (%s)", err, output)
	}
	return nil
}

func (m *Manager) HostDesktopTools() (HostDesktopTools, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	item, ok := m.installation(m.op.Installed)
	if m.closed || !ok || item.Contract != hostDesktopContract || item.Digest != m.pkg.Digest() {
		return HostDesktopTools{}, ErrUnsupported
	}
	root, err := m.installedDirectory(item.Digest)
	if err != nil {
		return HostDesktopTools{}, err
	}
	return ResolveHostDesktopTools(root, item.Architecture)
}
