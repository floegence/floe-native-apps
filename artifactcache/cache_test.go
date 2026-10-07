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
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
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

func TestSelectSourceChoosesFastestValidatedMirror(t *testing.T) {
	data := []byte("validated archive")
	sizes := int64(len(data))
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/canonical") {
			time.Sleep(30 * time.Millisecond)
		} else if strings.HasPrefix(r.URL.Path, "/slow") {
			time.Sleep(60 * time.Millisecond)
		}
		w.Header().Set("Content-Length", fmt.Sprint(sizes))
		if r.Method != http.MethodHead {
			_, _ = w.Write(data)
		}
	}))
	defer server.Close()
	makeSpec := func(path string) Spec {
		return Spec{URL: server.URL + "/canonical/" + path, Mirrors: []string{server.URL + "/slow/" + path, server.URL + "/fast/" + path}, SHA256: fmt.Sprintf("%x", sha256.Sum256(data)), SizeBytes: sizes}
	}
	source, err := SelectSource(t.Context(), []Spec{makeSpec("one"), makeSpec("two"), makeSpec("three")}, Options{Client: server.Client()})
	if err != nil || source != 2 {
		t.Fatalf("source=%d err=%v, want fastest mirror index 2", source, err)
	}
}

func TestAcquireFallsBackAcrossTrustedSources(t *testing.T) {
	data := []byte("validated archive")
	var canonicalCalls, mirrorCalls atomic.Int32
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasPrefix(r.URL.Path, "/canonical"):
			canonicalCalls.Add(1)
			_, _ = w.Write([]byte("bad archive"))
		default:
			mirrorCalls.Add(1)
			_, _ = w.Write(data)
		}
	}))
	defer server.Close()
	spec := Spec{URL: server.URL + "/canonical", Mirrors: []string{server.URL + "/mirror"}, SHA256: fmt.Sprintf("%x", sha256.Sum256(data)), SizeBytes: int64(len(data))}
	result, err := Acquire(t.Context(), t.TempDir(), spec, Options{Client: server.Client()})
	if err != nil || result.FromCache || canonicalCalls.Load() != 1 || mirrorCalls.Load() != 1 {
		t.Fatalf("result=%+v err=%v canonical=%d mirror=%d", result, err, canonicalCalls.Load(), mirrorCalls.Load())
	}
}

func TestAcquireCacheSkipsSourceProbesAndNetwork(t *testing.T) {
	data := []byte("validated archive")
	spec := Spec{URL: "https://canonical.example/archive", Mirrors: []string{"https://mirror.example/archive"}, SHA256: fmt.Sprintf("%x", sha256.Sum256(data)), SizeBytes: int64(len(data))}
	root := t.TempDir()
	client := &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
		return nil, errors.New("network must not be used for a verified cache")
	})}
	if err := os.WriteFile(filepath.Join(root, spec.SHA256), data, 0600); err != nil {
		t.Fatal(err)
	}
	result, err := Acquire(t.Context(), root, spec, Options{Client: client})
	if err != nil || !result.FromCache {
		t.Fatalf("result=%+v err=%v", result, err)
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) { return f(request) }
