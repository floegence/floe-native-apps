package nativeapps

import (
	"bytes"
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/floegence/floe-native-apps/artifactcache"
)

func TestBundleCacheProgressAndCrossArchitectureReuse(t *testing.T) {
	data := [][]byte{[]byte("first pinned original"), []byte("second pinned original")}
	var calls atomic.Int32
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1); _, _ = w.Write(data[1]) }))
	defer server.Close()
	pkg := Package{ID: "fixture", Architecture: "amd64", InstalledBytes: 1000}
	for i, b := range data {
		pkg.Artifacts = append(pkg.Artifacts, Artifact{Name: fmt.Sprintf("%d.apk", i), URL: server.URL, SHA256: fmt.Sprintf("%x", sha256.Sum256(b)), Size: int64(len(b)), Format: "apk"})
		pkg.SizeBytes += int64(len(b))
	}
	root := t.TempDir()
	archives := filepath.Join(root, "archives")
	if err := os.Mkdir(archives, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(archives, pkg.Artifacts[0].SHA256), data[0], 0600); err != nil {
		t.Fatal(err)
	}
	var progress []BundleProgress
	options := BundleOptions{Client: server.Client(), OnProgress: func(p BundleProgress) error { progress = append(progress, p); return nil }}
	var first bytes.Buffer
	if err := WriteBundleWithOptions(t.Context(), pkg, root, &first, options); err != nil {
		t.Fatal(err)
	}
	if calls.Load() != 1 || progress[0].Phase != "checking" || progress[0].DownloadBytes != nil {
		t.Fatal(calls.Load(), progress)
	}
	last := progress[len(progress)-1]
	if last.Phase != "packing" || *last.CachedBytes != int64(len(data[0])) || *last.DownloadBytes != int64(len(data[1])) || last.DownloadedBytes != int64(len(data[1])) {
		t.Fatal(last)
	}
	pkg.Architecture = "arm64"
	progress = nil
	// A different recipe/architecture may share exactly the same publisher bytes.
	server.Close()
	var second bytes.Buffer
	if err := WriteBundleWithOptions(t.Context(), pkg, root, &second, options); err != nil {
		t.Fatal(err)
	}
	last = progress[len(progress)-1]
	if *last.CachedBytes != pkg.SizeBytes || *last.DownloadBytes != 0 || last.DownloadedBytes != 0 || !bytes.Equal(first.Bytes(), second.Bytes()) {
		t.Fatal(last)
	}
	for _, p := range progress {
		if p.Phase == "downloading" {
			t.Fatal("cache verification presented as network download")
		}
	}
}

type observingWriter struct{ write func([]byte) (int, error) }

func (w observingWriter) Write(p []byte) (int, error) { return w.write(p) }

func TestBundleProtectsReadsAndMaintainsAfterPackingFailure(t *testing.T) {
	pkg, err := ForPlatform("linux", "amd64")
	if err != nil {
		t.Fatal(err)
	}
	b := []byte("fixture")
	pkg.Artifacts = pkg.Artifacts[:1]
	a := &pkg.Artifacts[0]
	a.SHA256 = fmt.Sprintf("%x", sha256.Sum256(b))
	a.Size = int64(len(b))
	pkg.SizeBytes = a.Size
	root := t.TempDir()
	archives := filepath.Join(root, "archives")
	if err := os.Mkdir(archives, 0700); err != nil {
		t.Fatal(err)
	}
	name := filepath.Join(archives, a.SHA256)
	if err := os.WriteFile(name, b, 0600); err != nil {
		t.Fatal(err)
	}
	old := time.Now().Add(-8 * 24 * time.Hour)
	if err := os.Chtimes(name, old, old); err != nil {
		t.Fatal(err)
	}
	failure := errors.New("transfer disk full")
	maintained := false
	err = WriteBundleWithOptions(t.Context(), pkg, root, observingWriter{func(p []byte) (int, error) {
		if _, err := artifactcache.Maintain(t.Context(), archives, artifactcache.Policy{MaxBytes: 1}, true); !errors.Is(err, artifactcache.ErrBusy) {
			t.Fatal("reader lease lost", err)
		}
		if _, err := os.Stat(name); err != nil {
			t.Fatal("active archive removed", err)
		}
		return 0, failure
	}}, BundleOptions{CachePolicy: artifactcache.Policy{MaxIdleAge: 7 * 24 * time.Hour}, OnMaintenance: func(report artifactcache.Maintenance, err error) {
		maintained = true
		if err != nil || report.RemovedFiles != 1 {
			t.Fatal(report, err)
		}
	}})
	if !errors.Is(err, failure) || !maintained {
		t.Fatal(err, maintained)
	}
}

func TestBundleCancellationPreservesAcquiredFiles(t *testing.T) {
	data := []byte("verified")
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write(data) }))
	defer server.Close()
	pkg := Package{ID: "fixture", Architecture: "amd64", InstalledBytes: 10, SizeBytes: int64(len(data)), Artifacts: []Artifact{{Name: "fixture.apk", URL: server.URL, Format: "apk", Size: int64(len(data)), SHA256: fmt.Sprintf("%x", sha256.Sum256(data))}}}
	root := t.TempDir()
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	err := WriteBundleWithOptions(ctx, pkg, root, &bytes.Buffer{}, BundleOptions{Client: server.Client(), OnProgress: func(p BundleProgress) error {
		if p.Phase == "packing" {
			cancel()
			return ctx.Err()
		}
		return nil
	}})
	if !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	if err := artifactcache.Verify(t.Context(), filepath.Join(root, "archives", pkg.Artifacts[0].SHA256), archiveSpec(pkg.Artifacts[0])); err != nil {
		t.Fatal(err)
	}
}
