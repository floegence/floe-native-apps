package nativeapps

import (
	"archive/zip"
	"context"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"

	"github.com/floegence/floe-native-apps/artifactcache"
)

// BundleProgress distinguishes verified cache bytes from actual network bytes.
// Totals remain absent until every requested archive has been checked.
type BundleProgress struct {
	Phase           string `json:"phase"`
	ComponentBytes  int64  `json:"component_bytes"`
	CachedBytes     *int64 `json:"cached_bytes,omitempty"`
	DownloadBytes   *int64 `json:"download_bytes,omitempty"`
	DownloadedBytes int64  `json:"downloaded_bytes"`
}
type BundleOptions struct {
	CachePolicy   artifactcache.Policy
	OnProgress    func(BundleProgress) error
	OnMaintenance func(artifactcache.Maintenance, error)
	// Client is a trusted network policy/test hook, never a renderer input.
	Client *http.Client
}

// WriteBundle acquires the exact catalog archives for an offline host. It neither
// extracts nor executes them; the destination host verifies every original byte.
// Callers must publish the output only after this function returns successfully.
func WriteBundle(ctx context.Context, pkg Package, cacheRoot string, output io.Writer, progress func(int64)) error {
	return WriteBundleWithOptions(ctx, pkg, cacheRoot, output, legacyBundleOptions(progress))
}
func legacyBundleOptions(progress func(int64)) BundleOptions {
	return BundleOptions{OnProgress: func(p BundleProgress) error {
		if progress != nil && p.CachedBytes != nil {
			progress(*p.CachedBytes + p.DownloadedBytes)
		}
		return nil
	}}
}

// WriteBundleWithOptions keeps the cache lease through the final ZIP read and
// applies optional maintenance afterwards, including on cancellation or failure.
func WriteBundleWithOptions(ctx context.Context, pkg Package, cacheRoot string, output io.Writer, options BundleOptions) error {
	if err := pkg.Validate(); err != nil {
		return err
	}
	return writeBundle(ctx, pkg.Artifacts, cacheRoot, output, options)
}

func writeBundle(ctx context.Context, artifacts []Artifact, cacheRoot string, output io.Writer, options BundleOptions) error {
	if !filepath.IsAbs(cacheRoot) || options.CachePolicy.MaxBytes < 0 || options.CachePolicy.MaxIdleAge < 0 {
		return ErrInvalid
	}
	var snapshot BundleProgress
	for _, a := range artifacts {
		snapshot.ComponentBytes += a.Size
	}
	report := func(phase string) error {
		snapshot.Phase = phase
		if err := ctx.Err(); err != nil {
			return err
		}
		if options.OnProgress != nil {
			return options.OnProgress(snapshot)
		}
		return nil
	}
	session, err := artifactcache.OpenSession(ctx, filepath.Join(cacheRoot, "archives"), artifactcache.SessionOptions{OnWait: func() error { return report("waiting") }})
	if err != nil {
		return err
	}
	defer session.Close()
	defer func() {
		result, err := session.Prune(context.WithoutCancel(ctx), options.CachePolicy)
		if options.OnMaintenance != nil {
			options.OnMaintenance(result, err)
		}
	}()
	if err := report("checking"); err != nil {
		return err
	}
	var cached, missing int64
	var downloads []Artifact
	for _, a := range artifacts {
		if err := session.Verify(ctx, archiveSpec(a)); err == nil {
			cached += a.Size
		} else if errors.Is(err, os.ErrNotExist) || errors.Is(err, artifactcache.ErrIntegrity) {
			missing += a.Size
			downloads = append(downloads, a)
		} else {
			return err
		}
	}
	snapshot.CachedBytes = &cached
	snapshot.DownloadBytes = &missing
	for _, a := range downloads {
		before := snapshot.DownloadedBytes
		_, err := session.Acquire(ctx, archiveSpec(a), artifactcache.Options{Client: options.Client, OnProgress: func(p artifactcache.Progress) error {
			if p.Phase != "downloading" {
				return ctx.Err()
			}
			snapshot.DownloadedBytes = before + p.ReceivedBytes
			return report("downloading")
		}})
		if err != nil {
			return err
		}
	}
	if err := report("packing"); err != nil {
		return err
	}
	archive := zip.NewWriter(output)
	for _, a := range artifacts {
		if err := ctx.Err(); err != nil {
			return err
		}
		header := &zip.FileHeader{Name: a.Name, Method: zip.Store}
		header.SetMode(0600)
		entry, err := archive.CreateHeader(header)
		if err != nil {
			return err
		}
		name, err := session.Path(archiveSpec(a))
		if err != nil {
			return err
		}
		file, err := os.Open(name)
		if err != nil {
			return err
		}
		_, err = io.Copy(entry, bundleContextReader{ctx, file})
		file.Close()
		if err != nil {
			return err
		}
	}
	if err := archive.Close(); err != nil {
		return err
	}
	for _, a := range artifacts {
		if err := session.Touch(archiveSpec(a)); err != nil {
			return err
		}
	}
	return ctx.Err()
}

type bundleContextReader struct {
	context.Context
	io.Reader
}

func (r bundleContextReader) Read(p []byte) (int, error) {
	if err := r.Err(); err != nil {
		return 0, err
	}
	return r.Reader.Read(p)
}
