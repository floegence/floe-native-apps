package artifactcache

import (
	"bufio"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestSessionPrunesIdleThenLeastRecentlyUsed(t *testing.T) {
	root := t.TempDir()
	session, err := OpenSession(t.Context(), root, SessionOptions{})
	if err != nil {
		t.Fatal(err)
	}
	defer session.Close()
	now := time.Now().Truncate(time.Second)
	put := func(n int, age time.Duration) string {
		name := fmt.Sprintf("%064x", n)
		full := filepath.Join(root, name)
		if err := os.WriteFile(full, []byte("1234"), 0600); err != nil {
			t.Fatal(err)
		}
		if err := os.Chtimes(full, now.Add(-age), now.Add(-age)); err != nil {
			t.Fatal(err)
		}
		return full
	}
	expired := put(1, 7*24*time.Hour+time.Second)
	boundary := put(2, 7*24*time.Hour)
	older := put(3, 2*time.Hour)
	latest := put(4, time.Hour)
	if err := os.WriteFile(filepath.Join(root, "notes"), []byte("keep"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, ".part-123"), []byte("partial"), 0600); err != nil {
		t.Fatal(err)
	}
	result, err := session.pruneAt(t.Context(), Policy{MaxIdleAge: 7 * 24 * time.Hour}, now)
	if err != nil || result.RemovedFiles != 2 {
		t.Fatal(result, err)
	}
	if _, err := os.Stat(expired); !errors.Is(err, os.ErrNotExist) {
		t.Fatal(err)
	}
	if _, err := os.Stat(boundary); err != nil {
		t.Fatal("exact boundary evicted", err)
	}
	result, err = session.pruneAt(t.Context(), Policy{MaxBytes: 8}, now)
	if err != nil || result.RemovedFiles != 1 {
		t.Fatal(result, err)
	}
	for _, name := range []string{older, latest, filepath.Join(root, "notes"), filepath.Join(root, ".cache-lock")} {
		if _, err := os.Stat(name); err != nil {
			t.Fatal(name, err)
		}
	}
}

func TestSessionSuccessfulReuseRefreshesLegacyModificationTime(t *testing.T) {
	spec, client, calls := fixture(t)
	root := t.TempDir()
	name := filepath.Join(root, spec.SHA256)
	if err := os.WriteFile(name, []byte("verified publisher archive"), 0600); err != nil {
		t.Fatal(err)
	}
	old := time.Now().Add(-30 * 24 * time.Hour)
	if err := os.Chtimes(name, old, old); err != nil {
		t.Fatal(err)
	}
	result, err := Acquire(t.Context(), root, spec, Options{Client: client})
	if err != nil || !result.FromCache || calls.Load() != 0 {
		t.Fatal(result, err)
	}
	report, err := Maintain(t.Context(), root, Policy{MaxIdleAge: 7 * 24 * time.Hour}, false)
	if err != nil || report.RemovedFiles != 0 {
		t.Fatal(report, err)
	}
}

func TestSessionWaitingCancellationAndMaintenanceIsolation(t *testing.T) {
	root := t.TempDir()
	session, err := OpenSession(t.Context(), root, SessionOptions{})
	if err != nil {
		t.Fatal(err)
	}
	defer session.Close()
	if _, err := Maintain(t.Context(), root, Policy{MaxBytes: 1}, true); !errors.Is(err, ErrBusy) {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(t.Context())
	waits := 0
	_, err = OpenSession(ctx, root, SessionOptions{OnWait: func() error { waits++; cancel(); return nil }})
	if !errors.Is(err, context.Canceled) || waits != 1 {
		t.Fatal(err, waits)
	}
	if _, err := OpenSession(t.Context(), root, SessionOptions{Try: true}); !errors.Is(err, ErrBusy) {
		t.Fatal("waiter released owner's lock", err)
	}
}

func TestSessionMaintenancePreservesUnknownAndSymlinkFiles(t *testing.T) {
	root := t.TempDir()
	outside := filepath.Join(t.TempDir(), "outside")
	if err := os.WriteFile(outside, []byte("unchanged"), 0600); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{strings.Repeat("a", 64), ".part-123"} {
		if err := os.Symlink(outside, filepath.Join(root, name)); err != nil {
			t.Skip(err)
		}
	}
	if err := os.Mkdir(filepath.Join(root, strings.Repeat("b", 64)), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, ".part-unknown"), []byte("keep"), 0600); err != nil {
		t.Fatal(err)
	}
	result, err := Maintain(t.Context(), root, Policy{MaxBytes: 1, MaxIdleAge: time.Nanosecond}, false)
	if err != nil || result.RemovedFiles != 0 {
		t.Fatal(result, err)
	}
	data, err := os.ReadFile(outside)
	if err != nil || string(data) != "unchanged" {
		t.Fatal(string(data), err)
	}
}

// Each child uses a real OS process and the public API, including Windows locks.
func TestCacheProcessHelper(t *testing.T) {
	mode := os.Getenv("FLOE_CACHE_HELPER")
	if mode == "" {
		return
	}
	root := os.Getenv("FLOE_CACHE_ROOT")
	if mode == "hold" {
		s, err := OpenSession(t.Context(), root, SessionOptions{})
		if err != nil {
			t.Fatal(err)
		}
		defer s.Close()
		fmt.Println("ready")
		_, _ = bufio.NewReader(os.Stdin).ReadString('\n')
		return
	}
	var spec Spec
	if err := json.Unmarshal([]byte(os.Getenv("FLOE_CACHE_SPEC")), &spec); err != nil {
		t.Fatal(err)
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM([]byte(os.Getenv("FLOE_CACHE_CA"))) {
		t.Fatal("invalid fixture certificate")
	}
	client := &http.Client{Transport: &http.Transport{TLSClientConfig: &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12}}}
	fmt.Println("ready")
	_, _ = bufio.NewReader(os.Stdin).ReadString('\n')
	if _, err := Acquire(t.Context(), root, spec, Options{Client: client}); err != nil {
		t.Fatal(err)
	}
}

func TestSeparateProcessesDownloadOnceAndRecoverExitedLease(t *testing.T) {
	data := []byte("cross-process pinned artifact")
	var calls atomic.Int32
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1); _, _ = w.Write(data) }))
	defer server.Close()
	spec := Spec{URL: server.URL, SHA256: fmt.Sprintf("%x", sha256.Sum256(data)), SizeBytes: int64(len(data))}
	encoded, _ := json.Marshal(spec)
	cert := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: server.Certificate().Raw})
	root := t.TempDir()
	start := func(mode string) (*exec.Cmd, func()) {
		command := exec.Command(os.Args[0], "-test.run=^TestCacheProcessHelper$")
		command.Env = append(os.Environ(), "FLOE_CACHE_HELPER="+mode, "FLOE_CACHE_ROOT="+root, "FLOE_CACHE_SPEC="+string(encoded), "FLOE_CACHE_CA="+string(cert))
		stdout, err := command.StdoutPipe()
		if err != nil {
			t.Fatal(err)
		}
		stdin, err := command.StdinPipe()
		if err != nil {
			t.Fatal(err)
		}
		command.Stderr = os.Stderr
		if err := command.Start(); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() {
			if command.ProcessState == nil {
				_ = command.Process.Kill()
				_ = command.Wait()
			}
			_ = stdin.Close()
		})
		reader := bufio.NewReader(stdout)
		line, err := reader.ReadString('\n')
		if err != nil || line != "ready\n" {
			t.Fatal(line, err)
		}
		return command, func() { _, _ = stdin.Write([]byte("go\n")) }
	}
	holder, _ := start("hold")
	if _, err := OpenSession(t.Context(), root, SessionOptions{Try: true}); !errors.Is(err, ErrBusy) {
		t.Fatal(err)
	}
	// Only terminate the exact task-owned process. The kernel must release its lease.
	if err := holder.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	_ = holder.Wait()
	var children []*exec.Cmd
	var release []func()
	for range 4 {
		child, goNow := start("acquire")
		children = append(children, child)
		release = append(release, goNow)
	}
	for _, goNow := range release {
		goNow()
	}
	for _, child := range children {
		if err := child.Wait(); err != nil {
			t.Fatal(err)
		}
	}
	if calls.Load() != 1 {
		t.Fatalf("downloaded %d times", calls.Load())
	}
	if err := Verify(t.Context(), filepath.Join(root, spec.SHA256), spec); err != nil {
		t.Fatal(err)
	}
}
