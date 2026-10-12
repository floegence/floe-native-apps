//go:build linux

package hostdesktop

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"errors"
	"io"
	"io/fs"
	"os"
	"path/filepath"

	"github.com/floegence/floe-native-apps/internal/servicearchive"
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
	if err := os.MkdirAll(destination, 0755); err != nil {
		return err
	}
	root, err := os.OpenRoot(destination)
	if err != nil {
		return err
	}
	defer root.Close()
	archive := tar.NewReader(compressed)
	var budget servicearchive.Budget
	for {
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
		if budget.Accept(header) != nil {
			return errLoginDeployment
		}
		if err = root.MkdirAll(filepath.Dir(header.Name), 0755); err != nil {
			return err
		}
		output, err := root.OpenFile(header.Name, os.O_CREATE|os.O_EXCL|os.O_WRONLY, os.FileMode(header.Mode))
		if err != nil {
			return err
		}
		_, copyErr := io.CopyN(output, archive, header.Size)
		// Set permissions on the opened file, without resolving its path again.
		// The privileged transaction can inherit umask 0077.
		modeErr := output.Chmod(os.FileMode(header.Mode))
		closeErr := output.Close()
		if copyErr != nil || modeErr != nil || closeErr != nil {
			return errLoginDeployment
		}
	}
	if _, err = io.Copy(io.Discard, io.LimitReader(compressed, 1<<20)); err != nil {
		return err
	}
	if err = fs.WalkDir(root.FS(), ".", func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() {
			return root.Chmod(path, 0755)
		}
		return nil
	}); err != nil {
		return err
	}
	for _, name := range []string{"floe/host-desktop/python3", "floe/host-desktop/host_desktop_drm.py", "floe/host-desktop/host_desktop_login_text.py", "usr/bin/python3"} {
		info, err := root.Lstat(name)
		if err != nil || !info.Mode().IsRegular() {
			return errLoginDeployment
		}
	}
	return nil
}
