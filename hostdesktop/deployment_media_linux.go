//go:build linux

package hostdesktop

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"errors"
	"io"
	"os"
	"path"
	"path/filepath"
	"strings"
)

func loginExtractMedia(ctx context.Context, source, destination string) error {
	file, err := os.Open(source)
	if err != nil {
		return err
	}
	defer file.Close()
	compressed, err := gzip.NewReader(file)
	if err != nil {
		return err
	}
	defer compressed.Close()
	archive := tar.NewReader(compressed)
	remaining := int64(1 << 30)
	for entries := 0; ; entries++ {
		if err := ctx.Err(); err != nil {
			return err
		}
		header, err := archive.Next()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return err
		}
		if entries >= 100000 || header.Typeflag != tar.TypeReg || header.Size < 0 || header.Size > 256<<20 || header.Size > remaining ||
			!filepath.IsLocal(header.Name) || path.Clean(header.Name) != header.Name || strings.Contains(header.Name, "\\") ||
			(header.Mode != 0644 && header.Mode != 0755) {
			return errLoginDeployment
		}
		remaining -= header.Size
		target := filepath.Join(destination, header.Name)
		if err = os.MkdirAll(filepath.Dir(target), 0755); err != nil {
			return err
		}
		output, err := os.OpenFile(target, os.O_CREATE|os.O_EXCL|os.O_WRONLY, os.FileMode(header.Mode))
		if err != nil {
			return err
		}
		_, copyErr := io.CopyN(output, archive, header.Size)
		closeErr := output.Close()
		if copyErr != nil || closeErr != nil {
			return errLoginDeployment
		}
		// The privileged transaction can inherit umask 0077.
		if err = os.Chmod(target, os.FileMode(header.Mode)); err != nil {
			return err
		}
	}
	if _, err = io.Copy(io.Discard, io.LimitReader(compressed, 1<<20)); err != nil {
		return err
	}
	if err = filepath.WalkDir(destination, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() {
			return os.Chmod(path, 0755)
		}
		return nil
	}); err != nil {
		return err
	}
	for _, name := range []string{"floe/host-desktop/python3", "floe/host-desktop/host_desktop_drm.py", "usr/bin/python3"} {
		info, err := os.Lstat(filepath.Join(destination, name))
		if err != nil || !info.Mode().IsRegular() {
			return errLoginDeployment
		}
	}
	return nil
}
