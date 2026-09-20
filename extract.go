package nativeapps

import (
	"archive/tar"
	"bufio"
	"compress/gzip"
	"context"
	"errors"
	"io"
	"os"
	"path"
	"path/filepath"
	"strings"
)

type deferredLink struct {
	name, target string
	hard         bool
}
type unpacker struct {
	root      string
	remaining int64
	files     map[string]bool
	links     []deferredLink
	entries   int
}

// APK members are independently gzipped signature, metadata, and data tar streams.
// Links are resolved only after every regular file has been written, so no archive
// entry can redirect a later write through a symlink.
func (u *unpacker) archive(ctx context.Context, file *os.File, format string) error {
	reader := bufio.NewReader(file)
	for {
		compressed, err := gzip.NewReader(reader)
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return err
		}
		compressed.Multistream(false)
		tarReader := tar.NewReader(compressed)
		for {
			if err := ctx.Err(); err != nil {
				compressed.Close()
				return err
			}
			header, err := tarReader.Next()
			if errors.Is(err, io.EOF) {
				break
			}
			if err != nil {
				compressed.Close()
				return err
			}
			if err = u.entry(header, tarReader, format); err != nil {
				compressed.Close()
				return err
			}
		}
		// Finish the member to validate the gzip checksum before moving to the next.
		_, err = io.Copy(io.Discard, io.LimitReader(compressed, 1<<20))
		compressed.Close()
		if err != nil {
			return err
		}
	}
}

func (u *unpacker) entry(h *tar.Header, body io.Reader, format string) error {
	u.entries++
	if u.entries > 100000 || h.Size < 0 || h.Size > u.remaining {
		return errors.New("native archive exceeds expansion budget")
	}
	u.remaining -= h.Size
	name := strings.TrimSuffix(strings.TrimPrefix(h.Name, "./"), "/")
	if name == "" || name == "." {
		return nil
	}
	if !filepath.IsLocal(name) || path.Clean(name) != name || strings.Contains(name, "\\") {
		return errors.New("invalid native archive path")
	}
	if format == "apk" && !strings.Contains(name, "/") && strings.HasPrefix(name, ".") {
		return nil
	}
	if format == "html5" {
		const prefix = "xpra-html5-20/html5/"
		if name == "xpra-html5-20/LICENSE" {
			name = "usr/share/licenses/xpra-html5/LICENSE"
		} else {
			if !strings.HasPrefix(name, prefix) {
				return nil
			}
			name = "usr/share/xpra/www/" + strings.TrimPrefix(name, prefix)
		}
	}
	if h.Typeflag == tar.TypeDir {
		return os.MkdirAll(filepath.Join(u.root, name), 0700)
	}
	if u.files[name] {
		return errors.New("duplicate native archive file")
	}
	u.files[name] = true
	if h.Typeflag == tar.TypeSymlink || h.Typeflag == tar.TypeLink {
		target := h.Linkname
		if strings.Contains(target, "\\") || strings.ContainsRune(target, 0) {
			return errors.New("invalid native archive link")
		}
		if strings.HasPrefix(target, "/") {
			target = strings.TrimPrefix(path.Clean(target), "/")
		} else if h.Typeflag == tar.TypeSymlink {
			target = path.Clean(path.Join(path.Dir(name), target))
		} else {
			target = path.Clean(strings.TrimPrefix(target, "./"))
		}
		if !filepath.IsLocal(target) {
			return errors.New("native archive link escapes package")
		}
		u.links = append(u.links, deferredLink{name, target, h.Typeflag == tar.TypeLink})
		return nil
	}
	if h.Typeflag != tar.TypeReg && h.Typeflag != tar.TypeRegA {
		return errors.New("unsupported native archive entry")
	}
	destination := filepath.Join(u.root, name)
	if err := os.MkdirAll(filepath.Dir(destination), 0700); err != nil {
		return err
	}
	output, err := os.OpenFile(destination, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600|os.FileMode(h.Mode)&0111)
	if err != nil {
		return err
	}
	_, err = io.CopyN(output, body, h.Size)
	closed := output.Close()
	if err != nil {
		return err
	}
	return closed
}

func (u *unpacker) finish() error {
	canonical, err := filepath.EvalSymlinks(u.root)
	if err != nil {
		return err
	}
	for _, link := range u.links {
		if !u.files[link.target] {
			info, err := os.Lstat(filepath.Join(u.root, link.target))
			if err != nil || !info.IsDir() {
				return errors.New("native archive link target missing")
			}
		}
		destination := filepath.Join(u.root, link.name)
		if err := os.MkdirAll(filepath.Dir(destination), 0700); err != nil {
			return err
		}
		if link.hard {
			continue
		}
		relative, err := filepath.Rel(filepath.Dir(destination), filepath.Join(u.root, link.target))
		if err != nil {
			return err
		}
		if err = os.Symlink(relative, destination); err != nil {
			return err
		}
	}
	for _, link := range u.links {
		if link.hard {
			if err := os.Link(filepath.Join(u.root, link.target), filepath.Join(u.root, link.name)); err != nil {
				return err
			}
		}
		resolved, err := filepath.EvalSymlinks(filepath.Join(u.root, link.name))
		if err != nil || !strings.HasPrefix(resolved, canonical+string(filepath.Separator)) {
			return errors.New("native archive link escapes installation")
		}
	}
	return nil
}
