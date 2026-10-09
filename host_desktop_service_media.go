package nativeapps

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"time"
)

// PrepareLoginScreenMedia acquires and checks the released media closure on
// the target as an ordinary SSH user. No service, driver or authority is changed.
func PrepareLoginScreenMedia(ctx context.Context, cache, output string, progress func(Status)) (string, error) {
	if runtime.GOOS != "linux" || !filepath.IsAbs(output) {
		return "", ErrUnsupported
	}
	pkg, err := HostDesktopForPlatform("linux", runtime.GOARCH)
	if err != nil {
		return "", err
	}
	manager, err := New(cache, pkg, nil)
	if err != nil {
		return "", err
	}
	defer manager.Close()
	watch, unsubscribe := manager.Watch()
	defer unsubscribe()
	const owner = "login-service-media"
	if _, err = manager.Start(owner, time.Now().Format(time.RFC3339Nano), "download", 0); err != nil {
		return "", err
	}
	for {
		status := manager.Snapshot(owner)
		if progress != nil {
			progress(status)
		}
		if !status.Active() {
			if status.State != "ready" {
				return "", errors.New("login service media preparation failed")
			}
			break
		}
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		case <-watch:
		}
	}
	tools, err := manager.HostDesktopTools()
	if err != nil {
		return "", err
	}
	return writeLoginScreenMedia(ctx, tools.Root, output)
}

// Resolve links inside the verified package and write only regular files.
// The root installer accepts no links, devices, paths or executable choices.
func writeLoginScreenMedia(ctx context.Context, root, output string) (string, error) {
	file, err := os.OpenFile(output, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return "", err
	}
	complete := false
	defer func() {
		_ = file.Close()
		if !complete {
			_ = os.Remove(output)
		}
	}()
	hash := sha256.New()
	compressed := gzip.NewWriter(io.MultiWriter(file, hash))
	archive := tar.NewWriter(compressed)
	err = filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if err = ctx.Err(); err != nil {
			return err
		}
		if entry.IsDir() {
			return nil
		}
		name, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		if info, err := os.Stat(path); err != nil {
			return err
		} else if info.IsDir() {
			return nil
		}
		resolved, err := containedDesktopFile(root, name)
		if err != nil {
			return err
		}
		input, err := os.Open(resolved)
		if err != nil {
			return err
		}
		defer input.Close()
		info, err := input.Stat()
		if err != nil {
			return err
		}
		mode := int64(0644)
		if info.Mode().Perm()&0111 != 0 {
			mode = 0755
		}
		if err = archive.WriteHeader(&tar.Header{Name: filepath.ToSlash(name), Mode: mode, Size: info.Size(), Typeflag: tar.TypeReg}); err != nil {
			return err
		}
		_, err = io.CopyN(archive, input, info.Size())
		return err
	})
	if err != nil {
		return "", err
	}
	if err = archive.Close(); err != nil {
		return "", err
	}
	if err = compressed.Close(); err != nil {
		return "", err
	}
	if err = file.Sync(); err != nil {
		return "", err
	}
	if err = file.Close(); err != nil {
		return "", err
	}
	complete = true
	return hex.EncodeToString(hash.Sum(nil)), nil
}
