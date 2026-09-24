// Package artifactcache acquires pinned publisher archives into a private,
// content-addressed cache. The trusted host owns the catalog, consent and paths;
// never construct a Spec from untrusted renderer or network input.
package artifactcache

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

var ErrInvalid = errors.New("invalid archive acquisition configuration")
var ErrIntegrity = errors.New("archive integrity check failed")

// Spec describes exact original publisher bytes selected by the trusted host.
type Spec struct {
	URL       string
	SHA256    string
	SizeBytes int64
}

// Progress reports absolute byte counts. Phase is checking, downloading or
// verifying. Cache validation performs no network request.
type Progress struct {
	Phase         string
	ReceivedBytes int64
	TotalBytes    int64
}

type Options struct {
	// Client is a trusted integration hook, primarily for network policy/tests.
	// Nil selects a client with a fifteen-minute timeout. HTTPS remains required.
	Client *http.Client
	// OnProgress may stop acquisition by returning an error.
	OnProgress func(Progress) error
}

type Result struct {
	Path      string
	FromCache bool
}

func (s Spec) validate() error {
	u, err := url.Parse(s.URL)
	hash, hashErr := hex.DecodeString(s.SHA256)
	if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.Fragment != "" || hashErr != nil || len(hash) != sha256.Size || strings.ToLower(s.SHA256) != s.SHA256 || s.SizeBytes <= 0 {
		return ErrInvalid
	}
	return nil
}

type contextReader struct {
	context.Context
	io.Reader
}

func (r contextReader) Read(p []byte) (int, error) {
	if err := r.Err(); err != nil {
		return 0, err
	}
	return r.Reader.Read(p)
}

// Verify checks an existing regular file against the pinned length and digest.
// It never changes the file. Cancellation and filesystem errors remain distinct
// from ErrIntegrity; missing files retain their os.ErrNotExist classification.
func Verify(ctx context.Context, name string, spec Spec) error {
	if err := spec.validate(); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	info, err := os.Lstat(name)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || info.Size() != spec.SizeBytes {
		return ErrIntegrity
	}
	file, err := os.Open(name)
	if err != nil {
		return err
	}
	defer file.Close()
	hash := sha256.New()
	n, err := io.Copy(hash, contextReader{ctx, io.LimitReader(file, spec.SizeBytes)})
	if err != nil {
		return err
	}
	var extra [1]byte
	more, tailErr := file.Read(extra[:])
	if tailErr != nil && tailErr != io.EOF {
		return tailErr
	}
	if n != spec.SizeBytes || more != 0 || hex.EncodeToString(hash.Sum(nil)) != spec.SHA256 {
		return ErrIntegrity
	}
	return ctx.Err()
}

// Acquire revalidates cached bytes or downloads and atomically publishes the
// exact archive. root must be an absolute private directory owned by the host.
// Concurrent calls share an exclusive cache lease and recheck after waiting.
// A verified cache entry is never deleted by cancellation. Returned paths must
// not be used concurrently with maintenance; use Session for protected reads.
func Acquire(ctx context.Context, root string, spec Spec, options Options) (Result, error) {
	if err := spec.validate(); err != nil {
		return Result{}, err
	}
	session, err := OpenSession(ctx, root, SessionOptions{})
	if err != nil {
		return Result{}, err
	}
	defer session.Close()
	return session.Acquire(ctx, spec, options)
}

func acquire(ctx context.Context, root string, spec Spec, options Options) (Result, error) {
	if err := spec.validate(); err != nil {
		return Result{}, err
	}
	if !filepath.IsAbs(root) {
		return Result{}, ErrInvalid
	}
	if err := ctx.Err(); err != nil {
		return Result{}, err
	}
	report := func(phase string, received int64) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if options.OnProgress != nil {
			return options.OnProgress(Progress{phase, received, spec.SizeBytes})
		}
		return nil
	}
	if err := report("checking", 0); err != nil {
		return Result{}, err
	}
	if err := os.MkdirAll(root, 0700); err != nil {
		return Result{}, err
	}
	info, err := os.Lstat(root)
	if err != nil {
		return Result{}, err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return Result{}, ErrInvalid
	}
	target := filepath.Join(root, spec.SHA256)
	if err := Verify(ctx, target, spec); err == nil {
		if err := report("checking", spec.SizeBytes); err != nil {
			return Result{}, err
		}
		return Result{Path: target, FromCache: true}, nil
	} else if !errors.Is(err, os.ErrNotExist) && !errors.Is(err, ErrIntegrity) {
		return Result{}, err
	}
	if err := report("downloading", 0); err != nil {
		return Result{}, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, spec.URL, nil)
	if err != nil {
		return Result{}, err
	}
	client := http.Client{Timeout: 15 * time.Minute}
	if options.Client != nil {
		client = *options.Client
	}
	redirect := client.CheckRedirect
	client.CheckRedirect = func(req *http.Request, via []*http.Request) error {
		if req.URL.Scheme != "https" || req.URL.User != nil {
			return ErrInvalid
		}
		if redirect != nil {
			return redirect(req, via)
		}
		if len(via) >= 10 {
			return errors.New("too many archive redirects")
		}
		return nil
	}
	response, err := client.Do(request)
	if err != nil {
		return Result{}, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return Result{}, fmt.Errorf("archive download returned HTTP %d", response.StatusCode)
	}
	if response.ContentLength >= 0 && response.ContentLength != spec.SizeBytes {
		return Result{}, ErrIntegrity
	}
	file, err := os.CreateTemp(root, ".part-")
	if err != nil {
		return Result{}, err
	}
	defer os.Remove(file.Name())
	defer file.Close()
	hash := sha256.New()
	writer := io.MultiWriter(file, hash)
	buffer := make([]byte, 128<<10)
	var received int64
	for {
		if err := ctx.Err(); err != nil {
			return Result{}, err
		}
		n, readErr := response.Body.Read(buffer)
		if n > 0 {
			if int64(n) > spec.SizeBytes-received {
				return Result{}, ErrIntegrity
			}
			if _, err := writer.Write(buffer[:n]); err != nil {
				return Result{}, err
			}
			received += int64(n)
			if err := report("downloading", received); err != nil {
				return Result{}, err
			}
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			return Result{}, readErr
		}
	}
	if err := report("verifying", received); err != nil {
		return Result{}, err
	}
	if received != spec.SizeBytes || hex.EncodeToString(hash.Sum(nil)) != spec.SHA256 {
		return Result{}, ErrIntegrity
	}
	if err := file.Sync(); err != nil {
		return Result{}, err
	}
	if err := file.Close(); err != nil {
		return Result{}, err
	}
	if err := ctx.Err(); err != nil {
		return Result{}, err
	}
	if err := os.Rename(file.Name(), target); err != nil {
		// Another acquirer may have published identical bytes first on a platform
		// that refuses replacement. Never remove that acquirer's cache entry.
		if verifyErr := Verify(ctx, target, spec); verifyErr != nil {
			return Result{}, err
		}
	}
	return Result{Path: target}, nil
}
