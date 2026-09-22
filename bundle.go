package nativeapps

import (
	"archive/zip"
	"context"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"time"
)

// WriteBundle acquires the exact catalog archives for an offline host. It neither
// extracts nor executes them; the destination host verifies every original byte.
// Callers must publish the output only after this function returns successfully.
func WriteBundle(ctx context.Context, pkg Package, cacheRoot string, output io.Writer, progress func(int64)) error {
	if err := pkg.Validate(); err != nil {
		return err
	}
	return writeBundle(ctx, pkg.Artifacts, cacheRoot, output, progress)
}

func writeBundle(ctx context.Context, artifacts []Artifact, cacheRoot string, output io.Writer, progress func(int64)) error {
	if !filepath.IsAbs(cacheRoot) {
		return ErrInvalid
	}
	if err := os.MkdirAll(filepath.Join(cacheRoot, "archives"), 0700); err != nil {
		return err
	}
	client := &http.Client{Timeout: 15 * time.Minute}
	var received int64
	for _, a := range artifacts {
		if err := downloadArchive(ctx, client, cacheRoot, a, func(n int64) error {
			received += n
			if progress != nil {
				progress(received)
			}
			return nil
		}); err != nil {
			return err
		}
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
		file, err := os.Open(filepath.Join(cacheRoot, "archives", a.SHA256))
		if err != nil {
			return err
		}
		_, err = io.Copy(entry, file)
		file.Close()
		if err != nil {
			return err
		}
	}
	return archive.Close()
}
