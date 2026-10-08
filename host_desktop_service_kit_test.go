package nativeapps

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestHostDesktopServiceArtifactsMatchReviewedBuild(t *testing.T) {
	for _, architecture := range []string{"amd64", "arm64"} {
		kit, err := LoginScreenServiceKit(architecture)
		if err != nil {
			t.Fatal(err)
		}
		for _, component := range []string{"drm", "service"} {
			base := filepath.Join("native", "host-desktop", component, "dist", architecture)
			data, err := os.ReadFile(filepath.Join(base, "manifest.json"))
			if err != nil {
				t.Fatal(err)
			}
			var manifest struct {
				Architecture string                 `json:"architecture"`
				Sources      map[string]string      `json:"sources"`
				Files        map[string]desktopFile `json:"files"`
			}
			if json.Unmarshal(data, &manifest) != nil || manifest.Architecture != architecture || len(manifest.Sources) < 4 || len(manifest.Files) == 0 {
				t.Fatal("invalid service build provenance")
			}
			for name, expected := range manifest.Sources {
				data, err := os.ReadFile(name)
				if err != nil {
					t.Fatal(err)
				}
				hash := sha256.Sum256(data)
				if hex.EncodeToString(hash[:]) != expected {
					t.Fatalf("rebuild %s after changing %s", component, name)
				}
			}
			for name, expected := range manifest.Files {
				if err := verifyDesktopFile(filepath.Join(base, name), expected); err != nil {
					t.Fatal(err)
				}
				hash := sha256.Sum256(kit.Files[name])
				if hex.EncodeToString(hash[:]) != expected.SHA256 {
					t.Fatal("service kit escaped preparation identity")
				}
			}
		}
		if kit.ServiceSHA256 == kit.WorkerSHA256 || len(kit.Files["libdrmtap.LICENSE"]) == 0 {
			t.Fatal("service kit missing identity or original license")
		}
	}
}
func TestHostDesktopServiceKitCancellationDoesNotLeaveFiles(t *testing.T) {
	kit, err := LoginScreenServiceKit("amd64")
	if err != nil {
		t.Fatal(err)
	}
	directory := filepath.Join(t.TempDir(), "kit")
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if kit.Write(ctx, directory) == nil {
		t.Fatal("canceled extraction accepted")
	}
	if _, err := os.Stat(directory); !os.IsNotExist(err) {
		t.Fatal("partial kit retained")
	}
	if err := kit.Write(context.Background(), directory); err != nil {
		t.Fatal(err)
	}
	for name := range kit.Files {
		info, err := os.Stat(filepath.Join(directory, name))
		if err != nil {
			t.Fatal(err)
		}
		expected := os.FileMode(0600)
		if hostDesktopExecutable(name) {
			expected = 0700
		}
		if info.Mode().Perm() != expected {
			t.Fatal("incorrect kit permissions")
		}
	}
}
