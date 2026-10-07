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
	Mirrors   []string
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
	// PreferredSource selects the first URL to try. -1 uses the canonical URL.
	// Remaining trusted URLs are tried in catalog order after it fails.
	PreferredSource int
	// OnProgress may stop acquisition by returning an error.
	OnProgress func(Progress) error
}

type Result struct {
	Path      string
	FromCache bool
}

func (s Spec) validate() error {
	urls := append([]string{s.URL}, s.Mirrors...)
	hash, hashErr := hex.DecodeString(s.SHA256)
	if hashErr != nil || len(hash) != sha256.Size || strings.ToLower(s.SHA256) != s.SHA256 || s.SizeBytes <= 0 || len(urls) == 0 {
		return ErrInvalid
	}
	seen := map[string]bool{}
	for _, rawURL := range urls {
		u, err := url.Parse(rawURL)
		if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.Fragment != "" || seen[rawURL] {
			return ErrInvalid
		}
		seen[rawURL] = true
	}
	return nil
}

func (s Spec) urls() []string { return append([]string{s.URL}, s.Mirrors...) }

const sourceProbeTimeout = 5 * time.Second

func clientWithRedirectPolicy(client *http.Client) *http.Client {
	if client == nil {
		client = &http.Client{Timeout: 15 * time.Minute}
	}
	copy := *client
	redirect := copy.CheckRedirect
	copy.CheckRedirect = func(req *http.Request, via []*http.Request) error {
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
	return &copy
}

func probeURL(ctx context.Context, client *http.Client, rawURL string, size int64) error {
	probe := func(method string, rangeHeader bool) (*http.Response, error) {
		probeCtx, cancel := context.WithTimeout(ctx, sourceProbeTimeout)
		defer cancel()
		request, err := http.NewRequestWithContext(probeCtx, method, rawURL, nil)
		if err != nil {
			return nil, err
		}
		if rangeHeader {
			request.Header.Set("Range", "bytes=0-0")
		}
		return client.Do(request)
	}
	response, err := probe(http.MethodHead, false)
	if err != nil {
		return err
	}
	response.Body.Close()
	if response.StatusCode >= http.StatusOK && response.StatusCode < http.StatusMultipleChoices && response.ContentLength == size {
		return nil
	}
	fallback := response.StatusCode == http.StatusMethodNotAllowed || response.StatusCode == http.StatusNotImplemented
	if response.StatusCode >= http.StatusOK && response.StatusCode < http.StatusMultipleChoices && response.ContentLength != size {
		fallback = true
	}
	if !fallback {
		return fmt.Errorf("archive probe returned HTTP %d", response.StatusCode)
	}
	response, err = probe(http.MethodGet, true)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode == http.StatusPartialContent {
		contentRange := response.Header.Get("Content-Range")
		if strings.HasPrefix(contentRange, "bytes 0-0/") && strings.TrimPrefix(contentRange, "bytes 0-0/") == fmt.Sprint(size) {
			return nil
		}
	}
	if response.StatusCode == http.StatusOK && response.ContentLength == size {
		return nil
	}
	return fmt.Errorf("archive probe returned HTTP %d", response.StatusCode)
}

// SelectSource probes a small representative set of trusted catalog entries and
// returns the source index with the lowest total probe latency. The canonical
// source remains the safe fallback when no source passes validation.
func SelectSource(ctx context.Context, specs []Spec, options Options) (int, error) {
	if len(specs) == 0 {
		return -1, ErrInvalid
	}
	for _, spec := range specs {
		if err := spec.validate(); err != nil {
			return -1, err
		}
	}
	sourceCount := len(specs[0].urls())
	for _, spec := range specs[1:] {
		if count := len(spec.urls()); count < sourceCount {
			sourceCount = count
		}
	}
	if sourceCount == 0 {
		return -1, ErrInvalid
	}
	representative := make([]int, 0, 3)
	for _, index := range []int{0, len(specs) / 2, len(specs) - 1} {
		duplicate := false
		for _, existing := range representative {
			if existing == index {
				duplicate = true
				break
			}
		}
		if !duplicate {
			representative = append(representative, index)
		}
	}
	client := clientWithRedirectPolicy(options.Client)
	bestIndex, bestLatency := 0, time.Duration(0)
	for source := 0; source < sourceCount; source++ {
		started := time.Now()
		valid := true
		for _, index := range representative {
			if err := probeURL(ctx, client, specs[index].urls()[source], specs[index].SizeBytes); err != nil {
				valid = false
				break
			}
		}
		if valid && (bestLatency == 0 || time.Since(started) < bestLatency) {
			bestIndex, bestLatency = source, time.Since(started)
		}
	}
	return bestIndex, nil
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
	urls := spec.urls()
	preferred := options.PreferredSource
	if preferred < 0 || preferred >= len(urls) {
		preferred = 0
	}
	order := make([]int, 0, len(urls))
	order = append(order, preferred)
	for index := range urls {
		if index != preferred {
			order = append(order, index)
		}
	}
	var lastErr error
	for _, source := range order {
		if err := acquireFromURL(ctx, clientWithRedirectPolicy(options.Client), urls[source], root, target, spec, report); err == nil {
			return Result{Path: target}, nil
		} else {
			lastErr = err
			if ctx.Err() != nil {
				return Result{}, ctx.Err()
			}
		}
	}
	return Result{}, lastErr
}

func acquireFromURL(ctx context.Context, client *http.Client, rawURL, root, target string, spec Spec, report func(string, int64) error) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return err
	}
	response, err := client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("archive download returned HTTP %d", response.StatusCode)
	}
	if response.ContentLength >= 0 && response.ContentLength != spec.SizeBytes {
		return ErrIntegrity
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
	var received int64
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		n, readErr := response.Body.Read(buffer)
		if n > 0 {
			if int64(n) > spec.SizeBytes-received {
				return ErrIntegrity
			}
			if _, err := writer.Write(buffer[:n]); err != nil {
				return err
			}
			received += int64(n)
			if err := report("downloading", received); err != nil {
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
	if err := report("verifying", received); err != nil {
		return err
	}
	if received != spec.SizeBytes || hex.EncodeToString(hash.Sum(nil)) != spec.SHA256 {
		return ErrIntegrity
	}
	if err := file.Sync(); err != nil {
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if err := os.Rename(file.Name(), target); err != nil {
		if verifyErr := Verify(ctx, target, spec); verifyErr != nil {
			return err
		}
	}
	return nil
}
