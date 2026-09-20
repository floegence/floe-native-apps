package nativeapps

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"
)

var ErrInvalid = errors.New("invalid native preparation request")
var ErrForbidden = errors.New("native preparation belongs to another caller")
var ErrBusy = errors.New("native preparation is already running")

type Status struct {
	State         string `json:"state"`
	OperationID   string `json:"operation_id,omitempty"`
	ReceivedBytes int64  `json:"received_bytes"`
	ExpectedBytes int64  `json:"expected_bytes"`
	ErrorCode     string `json:"error_code,omitempty"`
	CanCancel     bool   `json:"can_cancel"`
}

func (s Status) Active() bool {
	switch s.State {
	case "checking", "downloading", "receiving", "verifying", "installing", "validating":
		return true
	}
	return false
}

type operation struct {
	Version    int    `json:"version"`
	Package    string `json:"package"`
	Owner      string `json:"owner"`
	RequestID  string `json:"request_id"`
	Source     string `json:"source"`
	UploadSize int64  `json:"upload_size"`
	Status     Status `json:"status"`
}

type Manager struct {
	mu        sync.Mutex
	root      string
	pkg       Package
	op        operation
	client    *http.Client
	validate  func(context.Context, string) error
	prepare   func(context.Context, string, string) error
	ctx       context.Context
	cancel    context.CancelFunc
	done      chan struct{}
	upload    *os.File
	lease     *os.File
	watchers  map[chan struct{}]bool
	lastWrite time.Time
	closed    bool
}

// New requires a private state root. Creating a manager never downloads or starts
// native processes. The optional validator is useful for host-owned qualification;
// production callers use the built-in graphical self-check by passing nil.
func New(root string, pkg Package, validate func(context.Context, string) error) (*Manager, error) {
	if !filepath.IsAbs(root) {
		return nil, ErrInvalid
	}
	if err := pkg.Validate(); err != nil {
		return nil, err
	}
	if err := os.MkdirAll(root, 0700); err != nil {
		return nil, err
	}
	stat, err := os.Lstat(root)
	if err != nil || !stat.IsDir() || stat.Mode()&os.ModeSymlink != 0 {
		return nil, ErrInvalid
	}
	lease, err := os.OpenFile(filepath.Join(root, ".lock"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	if err = syscall.Flock(int(lease.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		lease.Close()
		return nil, ErrBusy
	}
	if validate == nil {
		validate = SelfTest
	}
	m := &Manager{root: root, pkg: pkg, client: &http.Client{Timeout: 15 * time.Minute}, validate: validate, prepare: prepareTools, lease: lease, watchers: map[chan struct{}]bool{}}
	m.op = operation{Version: 1, Package: pkg.Digest(), Status: Status{State: "available", ExpectedBytes: pkg.SizeBytes}}
	data, err := os.ReadFile(filepath.Join(root, "operation.json"))
	if err == nil {
		var saved operation
		if json.Unmarshal(data, &saved) != nil || saved.Version != 1 || !validState(saved.Status.State) {
			lease.Close()
			return nil, errors.New("invalid native preparation record")
		}
		if saved.Package == pkg.Digest() {
			m.op = saved
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		lease.Close()
		return nil, err
	}
	if m.installed() {
		m.op.Status.State = "ready"
		m.op.Status.ErrorCode = ""
	} else if m.op.Status.Active() {
		m.op.Status.State = "interrupted"
	} else if m.op.Status.State == "ready" {
		m.op.Status.State = "failed"
		m.op.Status.ErrorCode = "invalid_archive"
	}
	// This manager owns these staging names under the exclusive root lease.
	for _, pattern := range []string{".stage-*", ".upload-*", ".part-*", ".operation-*"} {
		paths, _ := filepath.Glob(filepath.Join(root, pattern))
		for _, path := range paths {
			if err := os.RemoveAll(path); err != nil {
				lease.Close()
				return nil, err
			}
		}
	}
	return m, nil
}

func (m *Manager) Package() Package  { return m.pkg }
func (m *Manager) directory() string { return filepath.Join(m.root, "packages", m.pkg.Digest()) }
func (m *Manager) installed() bool {
	data, err := os.ReadFile(filepath.Join(m.directory(), ".native-apps"))
	if err != nil || string(data) != m.pkg.Digest() {
		return false
	}
	_, err = ResolveTools(m.directory())
	return err == nil
}
func (m *Manager) Directory() (string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed || m.op.Status.State != "ready" || !m.installed() {
		return "", ErrInvalid
	}
	return m.directory(), nil
}
func (m *Manager) Snapshot(owner string) Status {
	m.mu.Lock()
	defer m.mu.Unlock()
	s := m.op.Status
	s.CanCancel = s.Active() && m.op.Owner == owner
	return s
}

func (m *Manager) Watch() (<-chan struct{}, func()) {
	m.mu.Lock()
	defer m.mu.Unlock()
	ch := make(chan struct{}, 1)
	m.watchers[ch] = true
	ch <- struct{}{}
	return ch, func() { m.mu.Lock(); delete(m.watchers, ch); m.mu.Unlock() }
}

func (m *Manager) publishLocked(force bool) error {
	if !force && time.Since(m.lastWrite) < 250*time.Millisecond {
		return nil
	}
	data, err := json.Marshal(m.op)
	if err != nil {
		return err
	}
	file, err := os.CreateTemp(m.root, ".operation-*")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	if _, err = file.Write(data); err == nil {
		err = file.Sync()
	}
	closed := file.Close()
	if err == nil {
		err = closed
	}
	if err == nil {
		err = os.Rename(file.Name(), filepath.Join(m.root, "operation.json"))
	}
	if err != nil {
		return err
	}
	m.lastWrite = time.Now()
	for ch := range m.watchers {
		select {
		case ch <- struct{}{}:
		default:
		}
	}
	return nil
}

func (m *Manager) Start(owner, requestID, source string, uploadSize int64) (Status, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed || owner == "" || requestID == "" || len(owner) > 256 || len(requestID) > 128 || (source != "download" && source != "upload") {
		return m.op.Status, ErrInvalid
	}
	if m.op.RequestID == requestID && m.op.Owner == owner {
		if m.op.Source != source || m.op.UploadSize != uploadSize {
			return m.op.Status, ErrInvalid
		}
		return m.snapshotLocked(owner), nil
	}
	if m.op.Status.Active() {
		return m.snapshotLocked(owner), ErrBusy
	}
	if m.installed() {
		m.op.Status.State = "ready"
		return m.snapshotLocked(owner), nil
	}
	if source == "upload" && (uploadSize < m.pkg.SizeBytes || uploadSize > m.pkg.SizeBytes+2<<20) {
		return m.op.Status, ErrInvalid
	}
	seed := make([]byte, 16)
	if _, err := rand.Read(seed); err != nil {
		return m.op.Status, err
	}
	m.ctx, m.cancel = context.WithCancel(context.Background())
	m.done = make(chan struct{})
	m.op = operation{Version: 1, Package: m.pkg.Digest(), Owner: owner, RequestID: requestID, Source: source, UploadSize: uploadSize, Status: Status{State: "downloading", OperationID: hex.EncodeToString(seed), ExpectedBytes: m.pkg.SizeBytes}}
	if source == "upload" {
		m.op.Status.State = "receiving"
		m.op.Status.ExpectedBytes = uploadSize
		file, err := os.CreateTemp(m.root, ".upload-*.zip")
		if err != nil {
			m.finishLocked("failed", classify(err, "install_failed"))
			return m.op.Status, err
		}
		m.upload = file
	}
	if err := m.publishLocked(true); err != nil {
		m.cancelLocked("failed")
		if m.done != nil {
			m.finishLocked("failed", "install_failed")
		}
		return m.op.Status, err
	}
	if source == "download" {
		go m.download(m.ctx)
	}
	return m.snapshotLocked(owner), nil
}

func (m *Manager) snapshotLocked(owner string) Status {
	s := m.op.Status
	s.CanCancel = s.Active() && owner == m.op.Owner
	return s
}
func (m *Manager) authorizeLocked(owner, id string) error {
	if owner == "" || owner != m.op.Owner {
		return ErrForbidden
	}
	if id == "" || id != m.op.Status.OperationID || m.closed {
		return ErrInvalid
	}
	return nil
}
func (m *Manager) WriteChunk(owner, id string, offset int64, data []byte) (Status, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.authorizeLocked(owner, id); err != nil {
		return m.op.Status, err
	}
	if m.upload == nil || len(data) == 0 || len(data) > 256<<10 || offset < 0 || offset+int64(len(data)) > m.op.Status.ExpectedBytes {
		return m.op.Status, ErrInvalid
	}
	if offset < m.op.Status.ReceivedBytes {
		previous := make([]byte, len(data))
		if _, err := m.upload.ReadAt(previous, offset); err != nil || !bytes.Equal(previous, data) {
			return m.op.Status, ErrInvalid
		}
		return m.snapshotLocked(owner), nil
	}
	if offset != m.op.Status.ReceivedBytes {
		return m.op.Status, ErrInvalid
	}
	n, err := m.upload.Write(data)
	m.op.Status.ReceivedBytes += int64(n)
	if err == nil {
		err = m.publishLocked(false)
	}
	if err != nil {
		m.cancelLocked("failed")
	}
	return m.snapshotLocked(owner), err
}
func (m *Manager) CompleteUpload(owner, id string) (Status, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.authorizeLocked(owner, id); err != nil {
		return m.op.Status, err
	}
	if m.upload == nil && m.op.Status.State != "receiving" {
		return m.snapshotLocked(owner), nil
	}
	if m.upload == nil || m.op.Status.ReceivedBytes != m.op.Status.ExpectedBytes {
		return m.op.Status, ErrInvalid
	}
	file := m.upload
	m.upload = nil
	m.op.Status.State = "verifying"
	if err := m.publishLocked(true); err != nil {
		file.Close()
		os.Remove(file.Name())
		m.finishLocked("failed", "install_failed")
		return m.op.Status, err
	}
	go m.receive(m.ctx, file)
	return m.snapshotLocked(owner), nil
}
func (m *Manager) Cancel(owner, id string) (Status, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.authorizeLocked(owner, id); err != nil {
		return m.op.Status, err
	}
	m.cancelLocked("cancelled")
	return m.snapshotLocked(owner), nil
}
func (m *Manager) cancelLocked(state string) {
	if !m.op.Status.Active() {
		return
	}
	if m.cancel != nil {
		m.cancel()
	}
	if m.upload != nil {
		m.upload.Close()
		os.Remove(m.upload.Name())
		m.upload = nil
		m.finishLocked(state, "")
	}
}
func (m *Manager) Close() {
	m.mu.Lock()
	m.closed = true
	m.cancelLocked("interrupted")
	done := m.done
	m.mu.Unlock()
	if done != nil {
		<-done
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.lease != nil {
		m.lease.Close()
		m.lease = nil
	}
}
func (m *Manager) finishLocked(state, code string) {
	m.op.Status.State = state
	m.op.Status.ErrorCode = code
	m.cancel = nil
	if err := m.publishLocked(true); err != nil {
		m.op.Status.State = "failed"
		m.op.Status.ErrorCode = "install_failed"
	}
	if m.done != nil {
		close(m.done)
		m.done = nil
	}
}
func (m *Manager) fail(ctx context.Context, err error, code string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	state := "failed"
	if ctx.Err() != nil {
		state = "cancelled"
		code = ""
		if m.closed {
			state = "interrupted"
		}
	}
	m.finishLocked(state, classify(err, code))
}
func validState(s string) bool {
	switch s {
	case "available", "checking", "downloading", "receiving", "verifying", "installing", "validating", "ready", "failed", "cancelled", "interrupted":
		return true
	}
	return false
}

func classify(err error, code string) string {
	if err != nil && strings.Contains(err.Error(), "native archive integrity") {
		return "invalid_archive"
	}
	if errors.Is(err, syscall.ENOSPC) {
		return "disk_full"
	}
	if errors.Is(err, os.ErrPermission) || errors.Is(err, syscall.EACCES) {
		return "permission_denied"
	}
	return code
}
func (m *Manager) stage(state string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.op.Status.State = state
	return m.publishLocked(true)
}
func (m *Manager) progress(n int64) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.op.Status.ReceivedBytes += n
	return m.publishLocked(false)
}
func (m *Manager) artifactPath(a Artifact) string { return filepath.Join(m.root, "archives", a.SHA256) }
func verifyFile(name string, a Artifact) bool {
	file, err := os.Open(name)
	if err != nil {
		return false
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() != a.Size {
		return false
	}
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return false
	}
	return fmt.Sprintf("%x", hash.Sum(nil)) == a.SHA256
}
func (m *Manager) download(ctx context.Context) {
	if err := os.MkdirAll(filepath.Join(m.root, "archives"), 0700); err != nil {
		m.fail(ctx, err, "install_failed")
		return
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	jobs := make(chan Artifact)
	var workers sync.WaitGroup
	var once sync.Once
	var failure error
	for i := 0; i < 4; i++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for a := range jobs {
				if err := m.downloadOne(ctx, a); err != nil {
					once.Do(func() { failure = err; cancel() })
				}
			}
		}()
	}
	for _, a := range m.pkg.Artifacts {
		select {
		case jobs <- a:
		case <-ctx.Done():
		}
	}
	close(jobs)
	workers.Wait()
	if failure != nil {
		m.fail(m.ctx, failure, "download_failed")
		return
	}
	if ctx.Err() != nil {
		m.fail(m.ctx, ctx.Err(), "download_failed")
		return
	}
	m.install(m.ctx)
}
func (m *Manager) downloadOne(ctx context.Context, a Artifact) error {
	return downloadArchive(ctx, m.client, m.root, a, m.progress)
}

func downloadArchive(ctx context.Context, client *http.Client, root string, a Artifact, progress func(int64) error) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if verifyFile(filepath.Join(root, "archives", a.SHA256), a) {
		return progress(a.Size)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, a.URL, nil)
	if err != nil {
		return err
	}
	response, err := client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK || (response.ContentLength >= 0 && response.ContentLength != a.Size) {
		return errors.New("unexpected native download response")
	}
	file, err := os.CreateTemp(root, ".part-")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	defer file.Close()
	hash := sha256.New()
	writer := io.MultiWriter(file, hash)
	buffer := make([]byte, 128<<10)
	var total int64
	for {
		n, readErr := response.Body.Read(buffer)
		if n > 0 {
			total += int64(n)
			if total > a.Size {
				return errors.New("native archive exceeds size")
			}
			if _, err = writer.Write(buffer[:n]); err != nil {
				return err
			}
			if err = progress(int64(n)); err != nil {
				return err
			}
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			return readErr
		}
	}
	if total != a.Size || fmt.Sprintf("%x", hash.Sum(nil)) != a.SHA256 {
		return errors.New("native archive integrity failure")
	}
	if err = file.Sync(); err != nil {
		return err
	}
	if err = file.Close(); err != nil {
		return err
	}
	return os.Rename(file.Name(), filepath.Join(root, "archives", a.SHA256))
}
func (m *Manager) receive(ctx context.Context, file *os.File) {
	defer file.Close()
	defer os.Remove(file.Name())
	info, err := file.Stat()
	if err != nil {
		m.fail(ctx, err, "invalid_archive")
		return
	}
	archive, err := zip.NewReader(file, info.Size())
	if err != nil || len(archive.File) != len(m.pkg.Artifacts) {
		m.fail(ctx, err, "invalid_archive")
		return
	}
	expected := map[string]Artifact{}
	for _, a := range m.pkg.Artifacts {
		expected[a.Name] = a
	}
	if err = os.MkdirAll(filepath.Join(m.root, "archives"), 0700); err != nil {
		m.fail(ctx, err, "install_failed")
		return
	}
	for _, entry := range archive.File {
		a, ok := expected[entry.Name]
		if !ok || entry.UncompressedSize64 != uint64(a.Size) || !entry.Mode().IsRegular() {
			m.fail(ctx, nil, "invalid_archive")
			return
		}
		delete(expected, entry.Name)
		if ctx.Err() != nil {
			m.fail(ctx, ctx.Err(), "")
			return
		}
		if verifyFile(m.artifactPath(a), a) {
			continue
		}
		input, err := entry.Open()
		if err != nil {
			m.fail(ctx, err, "invalid_archive")
			return
		}
		output, err := os.CreateTemp(m.root, ".part-")
		if err != nil {
			input.Close()
			m.fail(ctx, err, "install_failed")
			return
		}
		hash := sha256.New()
		_, err = io.Copy(io.MultiWriter(output, hash), io.LimitReader(input, a.Size+1))
		input.Close()
		output.Close()
		if err == nil && fmt.Sprintf("%x", hash.Sum(nil)) == a.SHA256 {
			err = os.Rename(output.Name(), m.artifactPath(a))
		} else if err == nil {
			err = errors.New("native archive integrity failure")
		}
		os.Remove(output.Name())
		if err != nil {
			m.fail(ctx, err, "invalid_archive")
			return
		}
	}
	m.install(ctx)
}
func (m *Manager) install(ctx context.Context) {
	if err := m.stage("installing"); err != nil {
		m.fail(ctx, err, "install_failed")
		return
	}
	staging, err := os.MkdirTemp(m.root, ".stage-")
	if err != nil {
		m.fail(ctx, err, "install_failed")
		return
	}
	defer os.RemoveAll(staging)
	u := &unpacker{root: staging, remaining: m.pkg.InstalledBytes, files: map[string]bool{}}
	for _, a := range m.pkg.Artifacts {
		file, err := os.Open(m.artifactPath(a))
		if err != nil {
			m.fail(ctx, err, "invalid_archive")
			return
		}
		err = u.archive(ctx, file, a.Format)
		file.Close()
		if err != nil {
			m.fail(ctx, err, "invalid_archive")
			return
		}
	}
	if err = u.finish(); err == nil {
		err = m.prepare(ctx, staging, m.pkg.Architecture)
	}
	if err != nil {
		m.fail(ctx, err, "install_failed")
		return
	}
	if err = m.stage("validating"); err == nil {
		err = m.validate(ctx, staging)
	}
	if err != nil {
		m.fail(ctx, err, "validation_failed")
		return
	}
	if err = os.WriteFile(filepath.Join(staging, ".native-apps"), []byte(m.pkg.Digest()), 0600); err != nil {
		m.fail(ctx, err, "install_failed")
		return
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if ctx.Err() != nil || m.closed {
		m.finishLocked("cancelled", "")
		return
	}
	if err = os.MkdirAll(filepath.Dir(m.directory()), 0700); err == nil {
		if !m.installed() {
			err = os.RemoveAll(m.directory())
		}
		if err == nil {
			err = os.Rename(staging, m.directory())
		}
	}
	if err != nil {
		m.finishLocked("failed", classify(err, "install_failed"))
		return
	}
	m.finishLocked("ready", "")
}
