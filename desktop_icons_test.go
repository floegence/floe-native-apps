package nativeapps

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDesktopIconDistribution(t *testing.T) {
	for _, arch := range []string{"amd64", "arm64"} {
		pkg, err := DesktopForPlatform("linux", arch)
		if err != nil {
			t.Fatal(err)
		}
		found := false
		for _, artifact := range pkg.Artifacts {
			if strings.HasPrefix(artifact.Name, "librsvg-") {
				found = true
			}
		}
		if !found {
			t.Errorf("%s desktop lacks SVG decoder", arch)
		}
		wrapper := desktopWrappers(arch)["python3"]
		for _, required := range []string{"GDK_PIXBUF_MODULE_FILE", "GDK_PIXBUF_MODULEDIR", ":$ROOT/usr/lib/gdk-pixbuf-2.0/2.10.0/loaders"} {
			if !strings.Contains(wrapper, required) {
				t.Errorf("%s Python lacks %s", arch, required)
			}
		}
	}
}

// The source test uses an explicit query-process fixture; native qualification
// exercises the actual musl loader and original decoder on both architectures.
func installDesktopImageLoaderFixture(t *testing.T, root, architecture string) {
	t.Helper()
	modules := filepath.Join(root, "usr/lib/gdk-pixbuf-2.0/2.10.0/loaders")
	if err := os.MkdirAll(modules, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(modules, "libpixbufloader_svg.so"), []byte("fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	loader := map[string]string{"amd64": "x86_64", "arm64": "aarch64"}[architecture]
	source := "#!/bin/sh\nprintf '\"%s/libpixbufloader_svg.so\"\\n\"svg\" 6 \"gdk-pixbuf\"\\n' \"$GDK_PIXBUF_MODULEDIR\"\n"
	if err := os.WriteFile(filepath.Join(root, "lib/ld-musl-"+loader+".so.1"), []byte(source), 0700); err != nil {
		t.Fatal(err)
	}
}

func TestDesktopImageCacheRelocationAndRetainedR2(t *testing.T) {
	for _, arch := range []string{"amd64", "arm64"} {
		t.Run(arch, func(t *testing.T) {
			parent := t.TempDir()
			root := filepath.Join(parent, "staging")
			for _, name := range desktopRequiredResources(arch) {
				path := filepath.Join(root, name)
				if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(path, []byte("fixture"), 0700); err != nil {
					t.Fatal(err)
				}
			}
			installDesktopImageLoaderFixture(t, root, arch)
			if err := prepareDesktopTools(t.Context(), root, arch); err != nil {
				t.Fatal(err)
			}
			cache, err := os.ReadFile(filepath.Join(root, "floe/desktop/pixbuf.loaders"))
			if err != nil || strings.Contains(string(cache), root) || !strings.Contains(string(cache), `"libpixbufloader_svg.so"`) {
				t.Fatal("nonrelocatable cache", string(cache), err)
			}
			installed := filepath.Join(parent, "installed")
			if err := os.Rename(root, installed); err != nil {
				t.Fatal(err)
			}
			if _, err := ResolveDesktopTools(installed, arch); err != nil {
				t.Fatal(err)
			}
			// Reconstruct the exact retained r2 wrapper/layout, without adding decoders.
			for name, contents := range desktopWrappersR2(arch) {
				if err := os.WriteFile(filepath.Join(installed, "floe/desktop/bin", name), []byte(contents), 0700); err != nil {
					t.Fatal(err)
				}
			}
			for _, name := range []string{"floe/desktop/pixbuf.loaders", "usr/lib/gdk-pixbuf-2.0/2.10.0/loaders/libpixbufloader_svg.so"} {
				if err := os.Remove(filepath.Join(installed, name)); err != nil {
					t.Fatal(err)
				}
			}
			if _, err := ResolveDesktopTools(installed, arch); err == nil {
				t.Fatal("new recipe accepted old layout")
			}
			if err := os.WriteFile(filepath.Join(installed, ".native-apps"), []byte(desktopR2Digests[arch]), 0600); err != nil {
				t.Fatal(err)
			}
			if _, err := ResolveDesktopTools(installed, arch); err != nil {
				t.Fatal("retained r2 rejected", err)
			}
			before, _ := os.ReadFile(filepath.Join(installed, "floe/desktop/bin/python3"))
			if string(before) != desktopWrappersR2(arch)["python3"] {
				t.Fatal("retained installation rewritten")
			}
		})
	}
}
