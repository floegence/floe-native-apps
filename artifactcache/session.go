package artifactcache

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"time"
)

var ErrBusy = errors.New("archive cache is in use")

// Policy is opt-in. Zero disables the corresponding eviction limit.
// MaxBytes counts verified archive files, not locks or temporary bundles.
type Policy struct {
	MaxIdleAge time.Duration
	MaxBytes   int64
}
type Maintenance struct {
	RemovedFiles int   `json:"removed_files"`
	RemovedBytes int64 `json:"removed_bytes"`
}
type SessionOptions struct {
	Try    bool
	OnWait func() error
}

// Session owns an exclusive OS lease until Close. Consumers must keep it open
// through their last archive read. Methods are used sequentially by the owner.
// A session is not an authorization boundary; root belongs to the trusted host.
type Session struct {
	root  string
	lease *os.File
}

func OpenSession(ctx context.Context, root string, options SessionOptions) (*Session, error) {
	if !filepath.IsAbs(root) {
		return nil, ErrInvalid
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if err := os.MkdirAll(root, 0700); err != nil {
		return nil, err
	}
	info, err := os.Lstat(root)
	if err != nil {
		return nil, err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return nil, ErrInvalid
	}
	name := filepath.Join(root, ".cache-lock")
	if info, err := os.Lstat(name); err == nil && !info.Mode().IsRegular() {
		return nil, ErrInvalid
	} else if err != nil && !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	file, err := os.OpenFile(name, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	acquired := false
	defer func() {
		if !acquired {
			file.Close()
		}
	}()
	notified := false
	for {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		err = lockCache(file)
		if err == nil {
			acquired = true
			return &Session{root: root, lease: file}, nil
		}
		if !errors.Is(err, ErrBusy) || options.Try {
			return nil, err
		}
		if !notified && options.OnWait != nil {
			if err := options.OnWait(); err != nil {
				return nil, err
			}
			notified = true
		}
		timer := time.NewTimer(25 * time.Millisecond)
		select {
		case <-ctx.Done():
			timer.Stop()
			return nil, ctx.Err()
		case <-timer.C:
		}
	}
}

func (s *Session) Close() error {
	if s.lease == nil {
		return nil
	}
	file := s.lease
	s.lease = nil
	return file.Close()
}

func (s *Session) Path(spec Spec) (string, error) {
	if s.lease == nil {
		return "", ErrInvalid
	}
	if err := spec.validate(); err != nil {
		return "", err
	}
	return filepath.Join(s.root, spec.SHA256), nil
}

func (s *Session) Verify(ctx context.Context, spec Spec) error {
	name, err := s.Path(spec)
	if err != nil {
		return err
	}
	return Verify(ctx, name, spec)
}

func (s *Session) Acquire(ctx context.Context, spec Spec, options Options) (Result, error) {
	if s.lease == nil {
		return Result{}, ErrInvalid
	}
	result, err := acquire(ctx, s.root, spec, options)
	if err == nil {
		err = s.Touch(spec)
	}
	return result, err
}

// Touch records successful use without changing archive bytes. Existing archives
// need no migration; their original modification time is the initial last use.
func (s *Session) Touch(spec Spec) error {
	name, err := s.Path(spec)
	if err != nil {
		return err
	}
	info, err := os.Lstat(name)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return ErrIntegrity
	}
	now := time.Now()
	return os.Chtimes(name, now, now)
}

var archiveName = regexp.MustCompile(`^[a-f0-9]{64}$`)
var stagingName = regexp.MustCompile(`^\.part-[0-9]+$`)

// Prune only removes recognized regular files under the exclusive lease. It
// never follows symlinks, unlinks lock files, or examines unrelated subdirectories.
func (s *Session) Prune(ctx context.Context, policy Policy) (Maintenance, error) {
	return s.pruneAt(ctx, policy, time.Now())
}
func (s *Session) pruneAt(ctx context.Context, policy Policy, now time.Time) (Maintenance, error) {
	var result Maintenance
	if s.lease == nil || policy.MaxIdleAge < 0 || policy.MaxBytes < 0 {
		return result, ErrInvalid
	}
	entries, err := os.ReadDir(s.root)
	if err != nil {
		return result, err
	}
	type entry struct {
		name string
		size int64
		used time.Time
	}
	var archives []entry
	var total int64
	remove := func(name string, size int64) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if err := os.Remove(filepath.Join(s.root, name)); err != nil {
			return err
		}
		result.RemovedFiles++
		result.RemovedBytes += size
		return nil
	}
	for _, e := range entries {
		if err := ctx.Err(); err != nil {
			return result, err
		}
		if !archiveName.MatchString(e.Name()) && !stagingName.MatchString(e.Name()) {
			continue
		}
		info, err := e.Info()
		if err != nil {
			return result, err
		}
		if !info.Mode().IsRegular() {
			continue
		}
		if stagingName.MatchString(e.Name()) || (policy.MaxIdleAge > 0 && now.Sub(info.ModTime()) > policy.MaxIdleAge) {
			if err := remove(e.Name(), info.Size()); err != nil {
				return result, err
			}
			continue
		}
		archives = append(archives, entry{e.Name(), info.Size(), info.ModTime()})
		total += info.Size()
	}
	sort.Slice(archives, func(i, j int) bool {
		if archives[i].used.Equal(archives[j].used) {
			return archives[i].name < archives[j].name
		}
		return archives[i].used.Before(archives[j].used)
	})
	for _, a := range archives {
		if policy.MaxBytes == 0 || total <= policy.MaxBytes {
			break
		}
		if err := remove(a.name, a.size); err != nil {
			return result, err
		}
		total -= a.size
	}
	return result, nil
}

// Maintain skips an active cache when try is true. Callers may log maintenance
// failures independently of an already successful installation or transfer.
func Maintain(ctx context.Context, root string, policy Policy, try bool) (Maintenance, error) {
	session, err := OpenSession(ctx, root, SessionOptions{Try: try})
	if err != nil {
		return Maintenance{}, err
	}
	defer session.Close()
	return session.Prune(ctx, policy)
}
