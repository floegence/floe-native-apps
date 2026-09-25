package nativeapps

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

// This explicit fixture checks original candidate archives through the same
// bounded extractor used by installation. It never activates a component or
// changes the compiled catalog. The supplied directory belongs to qualification.
func TestNativeDesktopCandidateExtraction(t *testing.T) {
	root := os.Getenv("FLOE_TEST_DESKTOP_CANDIDATE")
	if root == "" {
		t.Skip("explicit unpublished desktop component fixture")
	}
	if runtime.GOOS != "linux" || !filepath.IsAbs(root) {
		t.Fatal("absolute native Linux candidate directory required")
	}
	data, err := os.ReadFile(filepath.Join(root, "candidate.json"))
	if err != nil {
		t.Fatal(err)
	}
	var pkg Package
	if err := json.Unmarshal(data, &pkg); err != nil {
		t.Fatal(err)
	}
	if err := pkg.Validate(); err != nil || pkg.Architecture != runtime.GOARCH {
		t.Fatalf("invalid native candidate: %v", err)
	}
	output, err := os.MkdirTemp(root, "unactivated-")
	if err != nil {
		t.Fatal(err)
	}
	u := &unpacker{root: output, remaining: pkg.InstalledBytes, files: map[string]bool{}}
	for _, artifact := range pkg.Artifacts {
		path := filepath.Join(root, "apks", artifact.Name)
		if !verifyFile(path, artifact) {
			t.Fatalf("candidate archive verification: %s", artifact.Name)
		}
		file, err := os.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		err = u.archive(context.Background(), file, artifact.Format)
		_ = file.Close()
		if err != nil {
			t.Fatalf("candidate archive %s: %v", artifact.Name, err)
		}
	}
	if err := u.finish(); err != nil {
		t.Fatal(err)
	}
	record, err := json.Marshal(map[string]any{"root": output, "candidate_sha256": pkg.Digest(), "activated": false})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "extraction.json"), append(record, '\n'), 0600); err != nil {
		t.Fatal(err)
	}
	t.Logf("verified native %s candidate %s extracted to %s; not activated", runtime.GOARCH, pkg.Digest(), output)
}
