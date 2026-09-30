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
	"os/exec"
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
	marker, _ := os.ReadFile(filepath.Join(root, ".native-apps"))
	retainedR2 := string(marker) == desktopR2Digests[architecture]
	required := desktopRequiredResources(architecture)
	if !retainedR2 {
		required = append(required, "usr/lib/gdk-pixbuf-2.0/2.10.0/loaders/libpixbufloader_svg.so", "floe/desktop/pixbuf.loaders")
	}
	for _, name := range required {
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
	wrappers := desktopWrappers(architecture)
	if retainedR2 {
		wrappers = desktopWrappersR2(architecture)
	}
	for name, contents := range wrappers {
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
	return desktopRecipeWrappers(architecture, true)
}

// Retained r2 installations must pass their original wrapper contract unchanged.
func desktopWrappersR2(architecture string) map[string]string {
	return desktopRecipeWrappers(architecture, false)
}

func desktopRecipeWrappers(architecture string, imageLoaders bool) map[string]string {
	loader := map[string]string{"amd64": "x86_64", "arm64": "aarch64"}[architecture]
	result := map[string]string{}
	for _, name := range []string{"python3", "ibus-daemon", "ibus-portal", "gio-launch-desktop"} {
		binary := "$ROOT/floe/desktop/artifacts/" + name
		libraries := "$ROOT/floe/desktop/artifacts:$ROOT/lib:$ROOT/usr/lib"
		prefix := "#!/bin/sh\nROOT=$(CDPATH= cd -- \"$(dirname -- \"$0\")/../../..\" && pwd) || exit 1\n"
		if name == "python3" {
			binary = "$ROOT/usr/bin/python3"
			prefix += "export PYTHONHOME=\"$ROOT/usr\" PYTHONNOUSERSITE=1\nexport GI_TYPELIB_PATH=\"$ROOT/usr/lib/girepository-1.0\" GIO_MODULE_DIR=\"$ROOT/usr/lib/gio/modules\"\nexport GIO_LAUNCH_DESKTOP=\"$ROOT/floe/desktop/bin/gio-launch-desktop\"\n"
			if imageLoaders {
				prefix += "export GDK_PIXBUF_MODULE_FILE=\"$ROOT/floe/desktop/pixbuf.loaders\" GDK_PIXBUF_MODULEDIR=\"$ROOT/usr/lib/gdk-pixbuf-2.0/2.10.0/loaders\"\n"
				libraries += ":$ROOT/usr/lib/gdk-pixbuf-2.0/2.10.0/loaders"
			}
		}
		if name == "gio-launch-desktop" {
			binary = "$ROOT/usr/libexec/gio-launch-desktop"
		}
		prefix += "unset PYTHONPATH GIO_EXTRA_MODULES GTK_PATH LD_PRELOAD LD_LIBRARY_PATH\n"
		result[name] = prefix + "exec \"$ROOT/lib/ld-musl-" + loader + ".so.1\" --library-path \"" + libraries + "\" \"" + binary + "\" \"$@\"\n"
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
	if err := prepareDesktopImageLoaders(ctx, root, architecture); err != nil {
		return err
	}
	if _, err := ResolveDesktopTools(root, architecture); err != nil {
		return err
	}
	complete = true
	return nil
}

func prepareDesktopImageLoaders(ctx context.Context, root, architecture string) error {
	var err error
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return err
	}
	loader := map[string]string{"amd64": "x86_64", "arm64": "aarch64"}[architecture]
	modules := filepath.Join(root, "usr/lib/gdk-pixbuf-2.0/2.10.0/loaders")
	if _, err := containedDesktopFile(root, "usr/lib/gdk-pixbuf-2.0/2.10.0/loaders/libpixbufloader_svg.so"); err != nil {
		return err
	}
	command := exec.CommandContext(ctx, filepath.Join(root, "lib/ld-musl-"+loader+".so.1"),
		"--library-path", filepath.Join(root, "lib")+":"+filepath.Join(root, "usr/lib"), filepath.Join(root, "usr/bin/gdk-pixbuf-query-loaders"))
	command.Env = append(supportToolEnvironment(os.Environ(), filepath.Join(root, "floe/desktop/bin")), "GDK_PIXBUF_MODULEDIR="+modules)
	data, err := command.Output()
	if err != nil {
		return fmt.Errorf("desktop image loaders: %w", err)
	}
	if !strings.Contains(string(data), `"svg"`) || !strings.Contains(string(data), `libpixbufloader_svg.so"`) {
		return errors.New("desktop SVG image loader unavailable")
	}
	// Basenames resolve through Python's private loader path after atomic rename.
	cache := strings.ReplaceAll(string(data), modules+"/", "")
	return os.WriteFile(filepath.Join(root, "floe/desktop/pixbuf.loaders"), []byte(cache), 0600)
}

func checkDesktopImages(ctx context.Context, tools DesktopTools) error {
	const probe = `import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf
loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
loader.write(b'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16" fill="#13579b"/></svg>')
loader.close()
image = loader.get_pixbuf()
assert image.get_width() == 16 and image.get_height() == 16
assert bytes(image.get_pixels()[:3]) == bytes([19, 87, 155])
success, data = image.save_to_bufferv("png", [], [])
assert success
png = GdkPixbuf.PixbufLoader.new_with_type("png")
png.write(data)
png.close()
assert bytes(png.get_pixbuf().get_pixels()[:3]) == bytes([19, 87, 155])
`
	command := exec.CommandContext(ctx, tools.Python, "-B", "-c", probe)
	command.Env = tools.Environment(os.Environ())
	if output, err := command.CombinedOutput(); err != nil {
		return fmt.Errorf("desktop SVG/PNG self-check: %w (%s)", err, output)
	}
	return nil
}
