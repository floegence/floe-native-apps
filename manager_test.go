package nativeapps

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func testPackage(t *testing.T) (Package, []byte) {
	t.Helper()
	data := fixtureArchive(t, &tar.Header{Name: "payload", Typeflag: tar.TypeReg, Size: 4})
	hash := sha256.Sum256(data)
	a := Artifact{Name: "native.apk", URL: "https://example.invalid/native.apk", SHA256: fmt.Sprintf("%x", hash), Size: int64(len(data)), Format: "apk", License: "MIT", Source: "https://example.invalid/source"}
	return Package{ID: "fixture", Architecture: "amd64", SizeBytes: a.Size, InstalledBytes: 10000, Artifacts: []Artifact{a}}, data
}
func testManager(t *testing.T, validator func(context.Context, string) error) (*Manager, []byte) {
	t.Helper()
	p, data := testPackage(t)
	m, err := New(t.TempDir(), p, validator)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(m.Close)
	m.prepare = func(ctx context.Context, root, arch string) error {
		for _, name := range []string{"xpra", "python3", "Xvfb", "xauth", "dbus-run-session", "dbus-daemon", "xkbcomp"} {
			path := filepath.Join(root, "floe/bin", name)
			if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
				return err
			}
			if err := os.WriteFile(path, []byte("#!/bin/sh\nexit 0\n"), 0700); err != nil {
				return err
			}
		}
		for _, name := range []string{"index.html", "js/Client.js", "js/Window.js", "js/Utilities.js"} {
			path := filepath.Join(root, "usr/share/xpra/www", name)
			if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
				return err
			}
			if err := os.WriteFile(path, []byte("fixture"), 0600); err != nil {
				return err
			}
		}
		return nil
	}
	return m, data
}
func zipFixture(t *testing.T, name string, data []byte) []byte {
	t.Helper()
	var b bytes.Buffer
	z := zip.NewWriter(&b)
	w, err := z.CreateHeader(&zip.FileHeader{Name: name, Method: zip.Store})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = w.Write(data); err != nil {
		t.Fatal(err)
	}
	if err = z.Close(); err != nil {
		t.Fatal(err)
	}
	return b.Bytes()
}
func waitState(t *testing.T, m *Manager, expected string) {
	t.Helper()
	ch, stop := m.Watch()
	defer stop()
	timeout := time.NewTimer(3 * time.Second)
	defer timeout.Stop()
	for {
		select {
		case <-ch:
			s := m.Snapshot("owner")
			if s.State == expected {
				return
			}
			if !s.Active() {
				t.Fatalf("wanted %s, got %+v", expected, s)
			}
		case <-timeout.C:
			t.Fatalf("timeout: %+v", m.Snapshot("owner"))
		}
	}
}
func upload(t *testing.T, m *Manager, data []byte) Status {
	t.Helper()
	s, err := m.Start("owner", "request", "upload", int64(len(data)))
	if err != nil {
		t.Fatal(err)
	}
	if _, err = m.WriteChunk("owner", s.OperationID, 0, data); err != nil {
		t.Fatal(err)
	}
	if _, err = m.CompleteUpload("owner", s.OperationID); err != nil {
		t.Fatal(err)
	}
	return s
}
func TestVerifiedUploadActivatesAndSurvivesRestart(t *testing.T) {
	m, data := testManager(t, func(ctx context.Context, root string) error { return nil })
	upload(t, m, zipFixture(t, "native.apk", data))
	waitState(t, m, "ready")
	root, err := m.Directory()
	if err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(filepath.Join(root, "payload"))
	if err != nil || string(got) != "xxxx" {
		t.Fatal(err, string(got))
	}
	m.Close()
	next, err := New(m.root, m.pkg, func(context.Context, string) error { t.Fatal("revalidated on startup"); return nil })
	if err != nil {
		t.Fatal(err)
	}
	defer next.Close()
	if next.Snapshot("owner").State != "ready" {
		t.Fatal("lost committed installation")
	}
}
func TestUploadOwnershipReplayAndBounds(t *testing.T) {
	m, data := testManager(t, func(context.Context, string) error { return nil })
	bundle := zipFixture(t, "native.apk", data)
	s, err := m.Start("owner", "request", "upload", int64(len(bundle)))
	if err != nil {
		t.Fatal(err)
	}
	same, err := m.Start("owner", "request", "upload", int64(len(bundle)))
	if err != nil || same.OperationID != s.OperationID {
		t.Fatal("lost request idempotency")
	}
	if _, err = m.Start("other", "another", "download", 0); !errors.Is(err, ErrBusy) {
		t.Fatal(err)
	}
	if m.Snapshot("other").CanCancel {
		t.Fatal("another owner can cancel")
	}
	if _, err = m.Cancel("other", s.OperationID); !errors.Is(err, ErrForbidden) {
		t.Fatal(err)
	}
	if _, err = m.WriteChunk("owner", s.OperationID, 1, bundle[:2]); !errors.Is(err, ErrInvalid) {
		t.Fatal("accepted gap", err)
	}
	if _, err = m.WriteChunk("owner", s.OperationID, 0, bundle); err != nil {
		t.Fatal(err)
	}
	if _, err = m.WriteChunk("owner", s.OperationID, 0, bundle); err != nil {
		t.Fatal("lost chunk replay", err)
	}
	if _, err = m.WriteChunk("owner", s.OperationID, 0, []byte("changed")); !errors.Is(err, ErrInvalid) {
		t.Fatal("accepted conflicting replay")
	}
	if _, err = m.Cancel("owner", s.OperationID); err != nil {
		t.Fatal(err)
	}
	if _, err = m.Directory(); err == nil {
		t.Fatal("activated cancelled upload")
	}
}
func TestCancellationDuringValidationNeverActivates(t *testing.T) {
	started := make(chan struct{})
	m, data := testManager(t, func(ctx context.Context, root string) error { close(started); <-ctx.Done(); return ctx.Err() })
	s := upload(t, m, zipFixture(t, "native.apk", data))
	<-started
	if _, err := m.Cancel("owner", s.OperationID); err != nil {
		t.Fatal(err)
	}
	waitState(t, m, "cancelled")
	if _, err := os.Stat(m.directory()); !os.IsNotExist(err) {
		t.Fatal("cancelled installation exists", err)
	}
}
func TestInvalidArchiveNeverValidates(t *testing.T) {
	for _, kind := range []string{"hash", "name", "zip"} {
		t.Run(kind, func(t *testing.T) {
			m, data := testManager(t, func(context.Context, string) error { t.Error("untrusted bytes reached execution"); return nil })
			name := "native.apk"
			if kind == "hash" {
				data[0] ^= 1
			}
			if kind == "name" {
				name = "../native.apk"
			}
			bundle := zipFixture(t, name, data)
			if kind == "zip" {
				bundle[0] ^= 1
				bundle[len(bundle)-10] ^= 1
			}
			upload(t, m, bundle)
			waitState(t, m, "failed")
			if m.Snapshot("owner").ErrorCode != "invalid_archive" {
				t.Fatal(m.Snapshot("owner"))
			}
		})
	}
}
func TestPersistenceFailureDoesNotDeadlockClose(t *testing.T) {
	m, _ := testManager(t, func(context.Context, string) error { return nil })
	if err := os.Mkdir(filepath.Join(m.root, "operation.json"), 0700); err != nil {
		t.Fatal(err)
	}
	if _, err := m.Start("owner", "request", "download", 0); err == nil {
		t.Fatal("accepted failed persistence")
	}
	done := make(chan struct{})
	go func() { m.Close(); close(done) }()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("Close deadlocked")
	}
}
func TestRestartAndUnknownRecordFailClosed(t *testing.T) {
	p, _ := testPackage(t)
	root := t.TempDir()
	op := operation{Version: 1, Package: p.Digest(), Owner: "owner", RequestID: "request", Status: Status{State: "downloading", OperationID: "original"}}
	write := func() {
		b, _ := json.Marshal(op)
		if err := os.WriteFile(filepath.Join(root, "operation.json"), b, 0600); err != nil {
			t.Fatal(err)
		}
	}
	write()
	m, err := New(root, p, nil)
	if err != nil {
		t.Fatal(err)
	}
	if m.Snapshot("owner").State != "interrupted" {
		t.Fatal("resumed without consent")
	}
	m.Close()
	op.Status.State = "future"
	write()
	before, _ := os.ReadFile(filepath.Join(root, "operation.json"))
	if _, err = New(root, p, nil); err == nil {
		t.Fatal("accepted unknown state")
	}
	after, _ := os.ReadFile(filepath.Join(root, "operation.json"))
	if !bytes.Equal(before, after) {
		t.Fatal("rewrote unknown record")
	}
}
func TestDownloadChecksBytesAndReusesVerifiedCache(t *testing.T) {
	m, data := testManager(t, func(context.Context, string) error { return nil })
	calls := 0
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls++; _, _ = w.Write(data) }))
	defer server.Close()
	m.client = server.Client()
	a := m.pkg.Artifacts[0]
	a.URL = server.URL
	if err := os.MkdirAll(filepath.Join(m.root, "archives"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := m.downloadOne(context.Background(), a); err != nil {
		t.Fatal(err)
	}
	if err := m.downloadOne(context.Background(), a); err != nil {
		t.Fatal(err)
	}
	if calls != 1 {
		t.Fatal("redownloaded verified cache")
	}
	if err := os.WriteFile(m.artifactPath(a), []byte("corrupt"), 0600); err != nil {
		t.Fatal(err)
	}
	data = bytes.Repeat([]byte("z"), len(data))
	if err := m.downloadOne(context.Background(), a); err == nil {
		t.Fatal("accepted hash mismatch")
	}
	if verifyFile(m.artifactPath(a), a) {
		t.Fatal("activated corrupt cache")
	}
}
func TestRootLease(t *testing.T) {
	m, _ := testManager(t, func(context.Context, string) error { return nil })
	if _, err := New(m.root, m.pkg, nil); !errors.Is(err, ErrBusy) {
		t.Fatal("accepted second writer", err)
	}
}
