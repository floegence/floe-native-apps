package nativeapps

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRetiredImageAndCompressionArchivesRefreshPreservesInstalledRecipes(t *testing.T) {
	for _, recipe := range []struct {
		name     string
		resolve  func(string, string) (Package, error)
		previous map[string]string
	}{
		{"xpra", ForPlatform, map[string]string{"amd64": "5e33ac5c92aa81cfebd977c1c33ca41a02b4e8bfb186e481a5b67963f381be55", "arm64": "b1770cdb067af9510b9a02271010f5fa7b221d6c91d758889bce000c02d42974"}},
		{"desktop", DesktopForPlatform, map[string]string{"amd64": "15654ebb5038d29907cc076591149d1865d93c6c6fc7680f431ff248cd528b25", "arm64": "5386b382df3b4fb6c702a493d3abc0d1ab0ed0c9eb7f5c12dc577e10ea73a25f"}},
		{"host-desktop", HostDesktopForPlatform, map[string]string{"amd64": "d5c9c2ceda5c1aaa05b01901df7a88c27fb6e37c2e511a5f6477a1e104706605", "arm64": "73484fde471927a1479f340bd3f47ef03102a3902d71db77593e0334b2826c73"}},
	} {
		for architecture, previous := range recipe.previous {
			t.Run(recipe.name+"/"+architecture, func(t *testing.T) {
				pkg, err := recipe.resolve("linux", architecture)
				if err != nil {
					t.Fatal(err)
				}
				for _, artifact := range pkg.Artifacts {
					if strings.HasPrefix(artifact.Name, "zlib-") && artifact.Name != "zlib-1.3.2-r1.apk" || strings.HasPrefix(artifact.Name, "openjpeg-") && artifact.Name != "openjpeg-2.5.4-r2.apk" {
						t.Fatal("new preparation still selects a retired archive", artifact.Name)
					}
				}
				if pkg.Digest() == previous {
					t.Fatal("catalog update reused the published recipe identity")
				}
				root := t.TempDir()
				data, err := json.Marshal(operation{Version: 2, Package: previous, Installed: previous, Status: Status{State: "ready"}})
				if err != nil {
					t.Fatal(err)
				}
				record := filepath.Join(root, "operation.json")
				if err := os.WriteFile(record, data, 0600); err != nil {
					t.Fatal(err)
				}
				manager, err := New(root, pkg, nil)
				if err != nil {
					t.Fatal("published installation was rejected", err)
				}
				defer manager.Close()
				installed := manager.Snapshot("owner").Installed
				if installed == nil || installed.Digest != previous || installed.Ready {
					t.Fatal("previous identity was lost or absent files were trusted", installed)
				}
				after, err := os.ReadFile(record)
				if err != nil || !bytes.Equal(data, after) {
					t.Fatal("inspection rewrote the published installation record", err)
				}
			})
		}
	}
}

func TestRetiredTIFFArchiveRefreshPreservesPublishedRecipes(t *testing.T) {
	for _, recipe := range []struct {
		name     string
		resolve  func(string, string) (Package, error)
		previous map[string]string
	}{
		{"xpra", ForPlatform, map[string]string{"amd64": "9eefff9066f553bea3d29bc9b663e0a9f74bedaaa4f79c6494808354b3c85aa2", "arm64": "865686072d782cf22e83af5a997fc97c5cbb1d6b49814751749a2aac16a9c3b6"}},
		{"desktop", DesktopForPlatform, map[string]string{"amd64": "a118dce9ae9e997817962315f736ae8221652db08bc50cb21c987a578e973f7c", "arm64": "142cec4c4595835ab86779b75af47b21233af3642d9fc1f2ba18a860315e94cc"}},
		{"host-desktop", HostDesktopForPlatform, map[string]string{"amd64": "1139e68d94343c80a337bfddfcb20a541f332aa10206329516ab99c1f66040d2", "arm64": "0086d2de3dfbfc5d168d18cf6d2894397b78e902cd24573ccb9b76877188308f"}},
	} {
		for architecture, previous := range recipe.previous {
			t.Run(recipe.name+"/"+architecture, func(t *testing.T) {
				pkg, err := recipe.resolve("linux", architecture)
				if err != nil {
					t.Fatal(err)
				}
				found := false
				for _, artifact := range pkg.Artifacts {
					if strings.HasPrefix(artifact.Name, "tiff-") {
						found = true
						if artifact.Name != "tiff-4.7.2-r0.apk" {
							t.Fatal("retired TIFF archive remains selected", artifact.Name)
						}
					}
				}
				if !found || pkg.Digest() == previous {
					t.Fatal("TIFF refresh did not change the recipe")
				}
				retained := false
				for _, installed := range compatibleInstallations(pkg) {
					if installed.Digest == previous {
						retained = true
					}
				}
				if !retained {
					t.Fatal("published recipe identity was discarded", previous)
				}
			})
		}
	}
}
