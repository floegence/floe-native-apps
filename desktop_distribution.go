package nativeapps

import (
	"context"
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

const desktopContract = "wayland-xwayland-private-v1"

//go:embed desktop_catalog.json native/dist
var desktopDistribution embed.FS

// DesktopForPlatform returns the combined display recipe. ForPlatform continues
// to return the published Xpra recipe for consumers of that existing contract.
// Installation alone never certifies an arbitrary application's compatibility.
func DesktopForPlatform(platform, architecture string) (Package, error) {
	if platform != "linux" || (architecture != "amd64" && architecture != "arm64") {
		return Package{}, ErrUnsupported
	}
	data, err := desktopDistribution.ReadFile("desktop_catalog.json")
	if err != nil {
		return Package{}, err
	}
	var packages []Package
	if err := json.Unmarshal(data, &packages); err != nil {
		return Package{}, err
	}
	for _, pkg := range packages {
		if pkg.Architecture == architecture {
			return pkg, pkg.Validate()
		}
	}
	return Package{}, ErrUnsupported
}

type desktopFile struct {
	SHA256     string `json:"sha256"`
	Size       int64  `json:"size_bytes"`
	Common     bool   `json:"common"`
	Executable bool   `json:"executable"`
}
type desktopDistributionManifest struct {
	Version      int                    `json:"version"`
	Architecture string                 `json:"architecture"`
	Files        map[string]desktopFile `json:"files"`
}

func desktopManifest(architecture string) (desktopDistributionManifest, string, error) {
	var manifest desktopDistributionManifest
	if architecture != "amd64" && architecture != "arm64" {
		return manifest, "", ErrUnsupported
	}
	data, err := desktopDistribution.ReadFile("native/dist/" + architecture + "/manifest.json")
	if err != nil {
		return manifest, "", err
	}
	if err := json.Unmarshal(data, &manifest); err != nil {
		return manifest, "", err
	}
	if manifest.Version != 1 || manifest.Architecture != architecture || len(manifest.Files) == 0 {
		return manifest, "", ErrInvalid
	}
	var size int64
	for name, item := range manifest.Files {
		hash, err := hex.DecodeString(item.SHA256)
		if !filepath.IsLocal(name) || filepath.ToSlash(filepath.Clean(name)) != name || strings.Contains(name, "\\") ||
			err != nil || len(hash) != 32 || item.Size <= 0 || item.Size > 32<<20 {
			return manifest, "", ErrInvalid
		}
		size += item.Size
	}
	if size > 64<<20 {
		return manifest, "", ErrInvalid
	}
	digest := sha256.Sum256(data)
	return manifest, hex.EncodeToString(digest[:]), nil
}

// DesktopTools is the single verified layout used by planning and launch.
// It does not discover a host compositor or substitute a system input service.
type DesktopTools struct {
	Root, Python, Shell, Capture, Library, Xwayland, IBusDaemon, IBusPortal, QtPlugins, GTKModules string
}

func desktopRequiredResources(architecture string) []string {
	loader := map[string]string{"amd64": "x86_64", "arm64": "aarch64"}[architecture]
	return []string{"lib/ld-musl-" + loader + ".so.1", "usr/bin/python3", "usr/libexec/gio-launch-desktop",
		"usr/bin/weston", "usr/bin/Xwayland", "usr/bin/xauth", "usr/bin/xkbcomp", "usr/bin/dbus-daemon",
		"usr/bin/update-mime-database", "usr/bin/glib-compile-schemas", "usr/bin/gdk-pixbuf-query-loaders",
		"usr/bin/gtk-query-immodules-3.0", "usr/lib/libweston-14/headless-backend.so",
		"usr/libexec/xdg-desktop-portal", "usr/libexec/xdg-desktop-portal-gtk", "usr/libexec/xdg-document-portal",
		"usr/libexec/xdg-permission-store"}
}

func containedDesktopFile(root, name string) (string, error) {
	path := filepath.Join(root, name)
	real, err := filepath.EvalSymlinks(path)
	if err != nil {
		return "", err
	}
	relative, err := filepath.Rel(root, real)
	if err != nil || !filepath.IsLocal(relative) {
		return "", ErrInvalid
	}
	info, err := os.Stat(real)
	if err != nil || !info.Mode().IsRegular() {
		return "", ErrInvalid
	}
	return path, nil
}

func verifyDesktopFile(path string, expected desktopFile) error {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || info.Size() != expected.Size || expected.Executable && info.Mode()&0111 == 0 {
		return ErrInvalid
	}
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	opened, err := file.Stat()
	if err != nil || !os.SameFile(info, opened) {
		return ErrInvalid
	}
	hash := sha256.New()
	n, err := io.Copy(hash, io.LimitReader(file, expected.Size+1))
	if err != nil {
		return err
	}
	if n != expected.Size || hex.EncodeToString(hash.Sum(nil)) != expected.SHA256 {
		return errors.New("native desktop artifact integrity check failed")
	}
	return nil
}

// ResolveDesktopTools verifies installed derivations and the original executable
// closure without starting processes. Manager additionally checks recipe identity.
func ResolveDesktopTools(root, architecture string) (DesktopTools, error) {
	if !filepath.IsAbs(root) {
		return DesktopTools{}, ErrInvalid
	}
	root, err := filepath.EvalSymlinks(root)
	if err != nil {
		return DesktopTools{}, err
	}
	manifest, _, err := desktopManifest(architecture)
	if err != nil {
		return DesktopTools{}, err
	}
	for _, name := range desktopRequiredResources(architecture) {
		path, err := containedDesktopFile(root, name)
		if err != nil {
			return DesktopTools{}, err
		}
		if strings.HasPrefix(name, "usr/bin/") || strings.HasPrefix(name, "usr/libexec/") || strings.HasPrefix(name, "lib/ld-musl-") {
			info, err := os.Stat(path)
			if err != nil || info.Mode()&0111 == 0 {
				return DesktopTools{}, ErrInvalid
			}
		}
	}
	base := filepath.Join(root, "floe", "desktop")
	for name, expected := range manifest.Files {
		path, err := containedDesktopFile(root, filepath.Join("floe", "desktop", name))
		if err != nil {
			return DesktopTools{}, err
		}
		if err := verifyDesktopFile(path, expected); err != nil {
			return DesktopTools{}, err
		}
	}
	for name, contents := range desktopWrappers(architecture) {
		path, err := containedDesktopFile(root, filepath.Join("floe", "desktop", "bin", name))
		if err != nil {
			return DesktopTools{}, err
		}
		hash := sha256.Sum256([]byte(contents))
		if err := verifyDesktopFile(path, desktopFile{Size: int64(len(contents)), SHA256: hex.EncodeToString(hash[:]), Executable: true}); err != nil {
			return DesktopTools{}, err
		}
	}
	return DesktopTools{Root: root, Python: filepath.Join(base, "bin", "python3"), Shell: filepath.Join(base, "artifacts", "desktop-shell.so"),
		Capture: filepath.Join(base, "artifacts", "desktop-capture"), Library: filepath.Join(base, "artifacts", "libweston-14.so.0"),
		Xwayland: filepath.Join(base, "artifacts", "xwayland.so"), IBusDaemon: filepath.Join(base, "bin", "ibus-daemon"),
		IBusPortal: filepath.Join(base, "bin", "ibus-portal"), QtPlugins: filepath.Join(base, "qt"), GTKModules: filepath.Join(base, "gtk")}, nil
}

func desktopWrappers(architecture string) map[string]string {
	loader := map[string]string{"amd64": "x86_64", "arm64": "aarch64"}[architecture]
	result := map[string]string{}
	for _, name := range []string{"python3", "ibus-daemon", "ibus-portal", "gio-launch-desktop"} {
		binary := "$ROOT/floe/desktop/artifacts/" + name
		prefix := "#!/bin/sh\nROOT=$(CDPATH= cd -- \"$(dirname -- \"$0\")/../../..\" && pwd) || exit 1\n"
		if name == "python3" {
			binary = "$ROOT/usr/bin/python3"
			prefix += "export PYTHONHOME=\"$ROOT/usr\" PYTHONNOUSERSITE=1\nexport GI_TYPELIB_PATH=\"$ROOT/usr/lib/girepository-1.0\" GIO_MODULE_DIR=\"$ROOT/usr/lib/gio/modules\"\nexport GIO_LAUNCH_DESKTOP=\"$ROOT/floe/desktop/bin/gio-launch-desktop\"\n"
		}
		if name == "gio-launch-desktop" {
			binary = "$ROOT/usr/libexec/gio-launch-desktop"
		}
		prefix += "unset PYTHONPATH GIO_EXTRA_MODULES GTK_PATH LD_PRELOAD LD_LIBRARY_PATH\n"
		result[name] = prefix + "exec \"$ROOT/lib/ld-musl-" + loader + ".so.1\" --library-path \"$ROOT/floe/desktop/artifacts:$ROOT/lib:$ROOT/usr/lib\" \"" + binary + "\" \"$@\"\n"
	}
	return result
}

func prepareDesktopTools(ctx context.Context, root, architecture string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	manifest, _, err := desktopManifest(architecture)
	if err != nil {
		return err
	}
	base := filepath.Join(root, "floe", "desktop")
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
	for name, expected := range manifest.Files {
		if err := ctx.Err(); err != nil {
			return err
		}
		source := architecture
		if expected.Common {
			source = "common"
		}
		data, err := desktopDistribution.ReadFile("native/dist/" + source + "/" + name)
		if err != nil || int64(len(data)) != expected.Size || fmt.Sprintf("%x", sha256.Sum256(data)) != expected.SHA256 {
			return ErrInvalid
		}
		path := filepath.Join(base, name)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			return err
		}
		mode := os.FileMode(0600)
		if expected.Executable {
			mode = 0700
		}
		if err := os.WriteFile(path, data, mode); err != nil {
			return err
		}
	}
	if err := os.Mkdir(filepath.Join(base, "bin"), 0700); err != nil {
		return err
	}
	for name, contents := range desktopWrappers(architecture) {
		if err := os.WriteFile(filepath.Join(base, "bin", name), []byte(contents), 0700); err != nil {
			return err
		}
	}
	if _, err := ResolveDesktopTools(root, architecture); err != nil {
		return err
	}
	complete = true
	return nil
}
