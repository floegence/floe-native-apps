package nativeapps

import (
	"bytes"
	"context"
	"crypto/sha256"
	"debug/elf"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDesktopCatalogSeparatesRecipesAndRetainsXpra(t *testing.T) {
	for _, architecture := range []string{"amd64", "arm64"} {
		xpra, err := ForPlatform("linux", architecture)
		if err != nil {
			t.Fatal(err)
		}
		desktop, err := DesktopForPlatform("linux", architecture)
		if err != nil {
			t.Fatal(err)
		}
		if xpra.Preparation != nil || desktop.Preparation == nil || desktop.Digest() == xpra.Digest() {
			t.Fatal("new preparation must not change the published Xpra recipe")
		}
		items := compatibleInstallations(desktop)
		if len(items) != 4 || items[0].Contract != desktopContract || items[1].Digest != xpra.Digest() {
			t.Fatalf("surviving Xpra installations lost: %+v", items)
		}
		desktop.Preparation.NativeSHA256 = "unknown"
		if desktop.Validate() == nil {
			t.Fatal("unknown native preparation accepted")
		}
	}
	if _, err := DesktopForPlatform("darwin", "arm64"); err != ErrUnsupported {
		t.Fatal("desktop recipe admitted on unsupported platform", err)
	}
}

func TestDesktopDistributionProvenance(t *testing.T) {
	for _, architecture := range []string{"amd64", "arm64"} {
		manifest, _, err := desktopManifest(architecture)
		if err != nil {
			t.Fatal(err)
		}
		for _, name := range []string{"sources/weston-14.0.2.tar.xz", "sources/ibus-1.5.33.tar.gz",
			"sources/tree/native/protocols/text-input-unstable-v3.xml", "sources/tree/scripts/build_desktop_native.sh",
			"licenses/weston-14-COPYING.txt", "licenses/ibus-COPYING.txt", "licenses/LICENSE",
			"provenance/native.json", "provenance/qt5.json", "provenance/qt6.json", "provenance/gtk.json",
			"gtk/libfloe-gtk3-native.so", "gtk/libfloe-gtk4-native.so",
			"artifacts/desktop-shell.so", "artifacts/desktop-capture", "artifacts/libweston-14.so.0", "artifacts/xwayland.so", "artifacts/headless-backend.so",
			"artifacts/ibus-daemon", "artifacts/ibus-portal", "artifacts/libibus-1.0.so.5",
			"qt/platforminputcontexts/libfloe-client-native-qt5.so", "qt/platforminputcontexts/libfloe-client-native-qt6.so"} {
			if _, ok := manifest.Files[name]; !ok {
				t.Fatal("native distribution omitted an artifact or its corresponding source/notice", name)
			}
		}
		for name, item := range manifest.Files {
			source := architecture
			if item.Common {
				source = "common"
			}
			data, err := desktopDistribution.ReadFile("native/dist/" + source + "/" + name)
			if err != nil || int64(len(data)) != item.Size || fmt.Sprintf("%x", sha256.Sum256(data)) != item.SHA256 {
				t.Fatal("distribution hash mismatch", name, err)
			}
			if strings.HasPrefix(name, "sources/tree/") {
				current, err := os.ReadFile(strings.TrimPrefix(name, "sources/tree/"))
				if err != nil || !bytes.Equal(data, current) {
					t.Fatal("native build requires regeneration", name, err)
				}
			}
			if !strings.HasPrefix(name, "artifacts/") && !strings.HasPrefix(name, "qt/") && !strings.HasPrefix(name, "gtk/") {
				continue
			}
			binary, err := elf.NewFile(bytes.NewReader(data))
			if err != nil {
				t.Fatal(name, err)
			}
			machine := elf.EM_X86_64
			if architecture == "arm64" {
				machine = elf.EM_AARCH64
			}
			if binary.Machine != machine || binary.Type != elf.ET_DYN {
				t.Fatal("wrong native architecture", name)
			}
			for _, tag := range []elf.DynTag{elf.DT_RPATH, elf.DT_RUNPATH} {
				values, err := binary.DynString(tag)
				if err != nil || len(values) != 0 {
					t.Fatal("unexpected native loader path", name, values, err)
				}
			}
		}
	}
}

func TestDesktopManagerUpgradeRetainsPublishedXpra(t *testing.T) {
	root, _ := legacyInstallation(t)
	pkg, err := DesktopForPlatform("linux", "amd64")
	if err != nil {
		t.Fatal(err)
	}
	m, err := New(root, pkg, nil)
	if err != nil {
		t.Fatal(err)
	}
	status := m.Snapshot("owner")
	if status.Installed == nil || status.Installed.Digest != legacyAMD64 || !status.Installed.Ready || !status.UpdateAvailable {
		t.Fatal(status)
	}
	if _, err := m.DesktopBackend(); err != ErrUnsupported {
		t.Fatal("old recipe advertised combined capabilities", err)
	}
	oldRoot, err := m.DirectoryFor(legacyAMD64)
	if err != nil {
		t.Fatal(err)
	}
	before, err := os.ReadFile(filepath.Join(oldRoot, ".native-apps"))
	if err != nil {
		t.Fatal(err)
	}
	started, err := m.Start("owner", "cancel-before-upload", "upload", 1024)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := m.Cancel("owner", started.OperationID); err != nil {
		t.Fatal(err)
	}
	m.Close()
	m, err = New(root, pkg, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	if current, err := m.Directory(); err != nil || current != oldRoot {
		t.Fatal("restart lost previous recipe", current, err)
	}
	after, _ := os.ReadFile(filepath.Join(oldRoot, ".native-apps"))
	if !bytes.Equal(before, after) {
		t.Fatal("old installation rewritten")
	}
	data, _ := os.ReadFile(filepath.Join(root, "operation.json"))
	var saved operation
	if json.Unmarshal(data, &saved) != nil || saved.Version != 2 || saved.Installed != legacyAMD64 {
		t.Fatal("invalid retained selection")
	}
}

func TestDesktopPreparationVerifiesInstalledArtifactsAndContainment(t *testing.T) {
	root := t.TempDir()
	pkg, err := DesktopForPlatform("linux", "arm64")
	if err != nil {
		t.Fatal(err)
	}
	// Fake original resources isolate installation integrity from graphical
	// qualification; actual execution belongs to native installed-stack tests.
	for _, name := range desktopRequiredResources("arm64") {
		path := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("fixture"), 0700); err != nil {
			t.Fatal(err)
		}
	}
	if err := prepareDesktopTools(context.Background(), root, pkg.Architecture); err != nil {
		t.Fatal(err)
	}
	tools, err := ResolveDesktopTools(root, "arm64")
	if err != nil {
		t.Fatal(err)
	}
	if tools.Shell == "" || tools.IBusPortal == "" || tools.Python == "" {
		t.Fatal("incomplete installed resources")
	}
	if err := os.WriteFile(tools.Shell, []byte("damaged"), 0700); err != nil {
		t.Fatal(err)
	}
	if _, err := ResolveDesktopTools(root, "arm64"); err == nil {
		t.Fatal("damaged native shell accepted")
	}
	if err := prepareDesktopTools(context.Background(), root, "arm64"); err == nil {
		t.Fatal("prepared resources overwritten")
	}
}

func TestDesktopPreparationCancellationDoesNotLeaveResources(t *testing.T) {
	root := t.TempDir()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := prepareDesktopTools(ctx, root, "amd64"); err == nil {
		t.Fatal("cancelled preparation succeeded")
	}
	if _, err := os.Stat(filepath.Join(root, "floe", "desktop")); !os.IsNotExist(err) {
		t.Fatal("cancelled preparation left native resources", err)
	}
}

func TestDesktopPreparationRejectsNonExecutableOriginalTool(t *testing.T) {
	root := t.TempDir()
	for _, name := range desktopRequiredResources("amd64") {
		path := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("fixture"), 0700); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Chmod(filepath.Join(root, "usr/bin/python3"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := prepareDesktopTools(context.Background(), root, "amd64"); err == nil {
		t.Fatal("nonexecutable original Python was accepted")
	}
	if _, err := os.Lstat(filepath.Join(root, "floe/desktop")); !os.IsNotExist(err) {
		t.Fatal("failed preparation left partial resources", err)
	}
}

func TestDesktopFileVerificationRejectsOversizeAndLinks(t *testing.T) {
	directory := t.TempDir()
	path := filepath.Join(directory, "artifact")
	content := []byte("verified fixture")
	expected := desktopFile{Size: int64(len(content)), SHA256: fmt.Sprintf("%x", sha256.Sum256(content))}
	if err := os.WriteFile(path, content, 0600); err != nil {
		t.Fatal(err)
	}
	if err := verifyDesktopFile(path, expected); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(directory, "link")
	if err := os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	if err := verifyDesktopFile(link, expected); err == nil {
		t.Fatal("artifact symlink accepted")
	}
	if err := os.Truncate(path, 1<<34); err != nil {
		t.Fatal(err)
	}
	if err := verifyDesktopFile(path, expected); err == nil {
		t.Fatal("oversized sparse artifact accepted")
	}
}
