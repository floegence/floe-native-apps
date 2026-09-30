package nativeapps

import (
	"os"
	"path/filepath"
	"testing"
)

func TestHostDesktopRecipeCannotActivatePrivateApplicationTools(t *testing.T) {
	for _, architecture := range []string{"amd64", "arm64"} {
		pkg, err := HostDesktopForPlatform("linux", architecture)
		if err != nil {
			t.Fatal(err)
		}
		private, err := DesktopForPlatform("linux", architecture)
		if err != nil {
			t.Fatal(err)
		}
		if pkg.Digest() == private.Digest() || pkg.Preparation.Contract == private.Preparation.Contract {
			t.Fatal("physical and private desktops share a preparation identity")
		}
		installations := compatibleInstallations(pkg)
		if len(installations) != 1 || installations[0].Contract != hostDesktopContract {
			t.Fatal("host desktop adopted an unrelated private desktop")
		}
		pkg.Preparation.NativeSHA256 = private.Preparation.NativeSHA256
		if pkg.Validate() == nil {
			t.Fatal("wrong helper bytes passed preparation")
		}
	}
}

func TestHostDesktopPreparationDetectsModifiedNativeInputCode(t *testing.T) {
	root := t.TempDir()
	for _, name := range []string{"usr/bin/python3", "lib/ld-musl-x86_64.so.1", "usr/lib/gstreamer-1.0/libgstpipewire.so", "usr/lib/gstreamer-1.0/libgstx264.so", "usr/lib/gstreamer-1.0/libgstopus.so"} {
		path := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("fixture"), 0700); err != nil {
			t.Fatal(err)
		}
	}
	if err := prepareHostDesktopTools(t.Context(), root, "amd64"); err != nil {
		t.Fatal(err)
	}
	if _, err := ResolveHostDesktopTools(root, "amd64"); err != nil {
		t.Fatal(err)
	}
	input := filepath.Join(root, "floe", "host-desktop", "host_desktop_input.py")
	if err := os.WriteFile(input, []byte("changed authority"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := ResolveHostDesktopTools(root, "amd64"); err == nil {
		t.Fatal("changed helper was considered verified")
	}
}
