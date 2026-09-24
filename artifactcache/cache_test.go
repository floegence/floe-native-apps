package artifactcache

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
	"sync"
	"sync/atomic"
	"testing"
)

func fixture(t *testing.T) (Spec, *http.Client, *atomic.Int32) {
	t.Helper()
	data := []byte("verified publisher archive")
	calls := new(atomic.Int32)
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		_, _ = w.Write(data)
	}))
	t.Cleanup(server.Close)
	return Spec{URL: server.URL, SHA256: fmt.Sprintf("%x", sha256.Sum256(data)), SizeBytes: int64(len(data))}, server.Client(), calls
}

func TestAcquireVerifiesAndReusesCache(t *testing.T) {
	spec, client, calls := fixture(t)
	root := t.TempDir()
	var progress []Progress
	options := Options{Client: client, OnProgress: func(p Progress) error { progress = append(progress, p); return nil }}
	first, err := Acquire(t.Context(), root, spec, options)
	if err != nil || first.FromCache || first.Path != filepath.Join(root, spec.SHA256) {
		t.Fatalf("first: %+v %v", first, err)
	}
	second, err := Acquire(t.Context(), root, spec, options)
	if err != nil || !second.FromCache || calls.Load() != 1 {
		t.Fatalf("cache: %+v %v calls=%d", second, err, calls.Load())
	}
	if progress[0].Phase != "checking" || progress[len(progress)-1].ReceivedBytes != spec.SizeBytes {
		t.Fatal(progress)
	}
	if err := os.WriteFile(first.Path, bytes.Repeat([]byte("x"), int(spec.SizeBytes)), 0600); err != nil {
		t.Fatal(err)
	}
	if err := Verify(t.Context(), first.Path, spec); !errors.Is(err, ErrIntegrity) {
		t.Fatal(err)
	}
	third, err := Acquire(t.Context(), root, spec, options)
	if err != nil || third.FromCache || calls.Load() != 2 {
		t.Fatalf("repair: %+v %v", third, err)
	}
}

func TestInvalidAcquisitionNeverPublishes(t *testing.T) {
	for _, scenario := range []string{"digest", "short", "oversize", "cancel", "progress"} {
		t.Run(scenario, func(t *testing.T) {
			spec, client, _ := fixture(t)
			root := t.TempDir()
			ctx, cancel := context.WithCancel(t.Context())
			defer cancel()
			options := Options{Client: client}
			switch scenario {
			case "digest":
				spec.SHA256 = fmt.Sprintf("%x", sha256.Sum256([]byte("different")))
			case "short":
				spec.SizeBytes++
			case "oversize":
				spec.SizeBytes--
			case "cancel":
				options.OnProgress = func(p Progress) error {
					if p.Phase == "downloading" {
						cancel()
					}
					return nil
				}
			case "progress":
				options.OnProgress = func(p Progress) error {
					if p.ReceivedBytes > 0 {
						return errors.New("observer stopped")
					}
					return nil
				}
			}
			if _, err := Acquire(ctx, root, spec, options); err == nil {
				t.Fatal("accepted failed acquisition")
			}
			entries, _ := os.ReadDir(root)
			if len(entries) != 1 || entries[0].Name() != ".cache-lock" {
				t.Fatal("left transfer or published file", entries)
			}
		})
	}
}

func TestConcurrentAcquisitionAndCancellationKeepValidCache(t *testing.T) {
	spec, client, calls := fixture(t)
	root := t.TempDir()
	var workers sync.WaitGroup
	for range 8 {
		workers.Go(func() {
			if _, err := Acquire(t.Context(), root, spec, Options{Client: client}); err != nil {
				t.Error(err)
			}
		})
	}
	workers.Wait()
	if calls.Load() != 1 {
		t.Fatalf("concurrent consumers downloaded %d times", calls.Load())
	}
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	if _, err := Acquire(ctx, root, spec, Options{Client: client}); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	if err := Verify(t.Context(), filepath.Join(root, spec.SHA256), spec); err != nil {
		t.Fatal(err)
	}
	entries, _ := os.ReadDir(root)
	if len(entries) != 2 {
		t.Fatal(entries)
	}
}

func TestRejectsInvalidSpecsAndSymlinkEntries(t *testing.T) {
	spec, client, calls := fixture(t)
	for _, mutate := range []func(*Spec){func(s *Spec) { s.URL = "http://example.test/file" }, func(s *Spec) { s.SHA256 = "../escape" }, func(s *Spec) { s.SizeBytes = 0 }} {
		invalid := spec
		mutate(&invalid)
		if _, err := Acquire(t.Context(), t.TempDir(), invalid, Options{Client: client}); !errors.Is(err, ErrInvalid) {
			t.Fatal(err)
		}
	}
	if calls.Load() != 0 {
		t.Fatal("invalid spec accessed network")
	}
	root := t.TempDir()
	target := filepath.Join(t.TempDir(), "outside")
	if err := os.WriteFile(target, []byte("verified publisher archive"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, filepath.Join(root, spec.SHA256)); err != nil {
		t.Skip(err)
	}
	if err := Verify(t.Context(), filepath.Join(root, spec.SHA256), spec); !errors.Is(err, ErrIntegrity) {
		t.Fatal(err)
	}
	result, err := Acquire(t.Context(), root, spec, Options{Client: client})
	if err != nil || result.FromCache {
		t.Fatalf("symlink: %+v %v", result, err)
	}
	data, _ := os.ReadFile(target)
	if string(data) != "verified publisher archive" {
		t.Fatal("modified outside file")
	}
}
