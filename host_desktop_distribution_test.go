package nativeapps

import (
	"bytes"
	"encoding/json"
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
		releases, err := hostDesktopReleases()
		if err != nil {
			t.Fatal(err)
		}
		expected := map[string]bool{pkg.Digest(): true}
		for _, release := range releases {
			if release.Architecture == architecture {
				expected[release.Digest] = true
			}
		}
		if len(installations) != len(expected) {
			t.Fatal("host desktop lost a published installation")
		}
		for _, installed := range installations {
			if installed.Contract != hostDesktopContract || !expected[installed.Digest] || installed.Digest == private.Digest() {
				t.Fatal("host desktop adopted an unrelated private desktop")
			}
		}
		pkg.Preparation.NativeSHA256 = private.Preparation.NativeSHA256
		if pkg.Validate() == nil {
			t.Fatal("wrong helper bytes passed preparation")
		}
	}
}

func TestHostDesktopUpgradeAcceptsPublishedStateWithoutRewritingOldFiles(t *testing.T) {
	releases, err := hostDesktopReleases()
	if err != nil {
		t.Fatal(err)
	}
	for _, release := range releases {
		t.Run(release.Version+"/"+release.Architecture, func(t *testing.T) {
			root := t.TempDir()
			previous := release.Digest
			data, _ := json.Marshal(operation{Version: 2, Package: previous, Installed: previous, Status: Status{State: "ready"}})
			state := filepath.Join(root, "operation.json")
			if err := os.WriteFile(state, data, 0600); err != nil {
				t.Fatal(err)
			}
			pkg, err := HostDesktopForPlatform("linux", release.Architecture)
			if err != nil {
				t.Fatal(err)
			}
			m, err := New(root, pkg, nil)
			if err != nil {
				t.Fatal(err)
			}
			defer m.Close()
			status := m.Snapshot("owner")
			if status.Installed == nil || status.Installed.Digest != previous || status.Installed.Ready || status.State != "available" {
				t.Fatal("published identity was lost or missing files were trusted", status)
			}
			after, err := os.ReadFile(state)
			if err != nil || !bytes.Equal(data, after) {
				t.Fatal("opening an upgrade rewrote prior installation state", err)
			}
		})
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
