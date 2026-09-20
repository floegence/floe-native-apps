package nativeapps

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"os"
	"path/filepath"
	"testing"
)

func fixtureArchive(t *testing.T, entries ...*tar.Header) []byte {
	t.Helper()
	var b bytes.Buffer
	z := gzip.NewWriter(&b)
	w := tar.NewWriter(z)
	for _, h := range entries {
		if err := w.WriteHeader(h); err != nil {
			t.Fatal(err)
		}
		if h.Typeflag == tar.TypeReg {
			if _, err := w.Write(bytes.Repeat([]byte("x"), int(h.Size))); err != nil {
				t.Fatal(err)
			}
		}
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	if err := z.Close(); err != nil {
		t.Fatal(err)
	}
	return b.Bytes()
}
func unpack(t *testing.T, data []byte, budget int64) (*unpacker, error) {
	t.Helper()
	u := &unpacker{root: t.TempDir(), remaining: budget, files: map[string]bool{}}
	p := filepath.Join(t.TempDir(), "a.apk")
	if err := os.WriteFile(p, data, 0600); err != nil {
		t.Fatal(err)
	}
	f, err := os.Open(p)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	err = u.archive(context.Background(), f, "apk")
	if err == nil {
		err = u.finish()
	}
	return u, err
}
func TestArchiveRejectsUntrustedShapes(t *testing.T) {
	cases := map[string][]*tar.Header{
		"parent":         {{Name: "../escape", Typeflag: tar.TypeReg, Size: 1}},
		"absolute":       {{Name: "/escape", Typeflag: tar.TypeReg, Size: 1}},
		"duplicate":      {{Name: "bin/tool", Typeflag: tar.TypeReg, Size: 1}, {Name: "bin/tool", Typeflag: tar.TypeReg, Size: 1}},
		"device":         {{Name: "device", Typeflag: tar.TypeChar}},
		"escaping link":  {{Name: "bin/tool", Typeflag: tar.TypeSymlink, Linkname: "../../../outside"}},
		"link cycle":     {{Name: "a", Typeflag: tar.TypeSymlink, Linkname: "b"}, {Name: "b", Typeflag: tar.TypeSymlink, Linkname: "a"}},
		"link overwrite": {{Name: "bin", Typeflag: tar.TypeSymlink, Linkname: "usr"}, {Name: "bin/tool", Typeflag: tar.TypeReg, Size: 1}, {Name: "usr", Typeflag: tar.TypeDir}},
		"missing target": {{Name: "bin/tool", Typeflag: tar.TypeSymlink, Linkname: "missing"}},
	}
	for name, headers := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := unpack(t, fixtureArchive(t, headers...), 1000); err == nil {
				t.Fatal("accepted unsafe archive")
			}
		})
	}
}
func TestArchiveMultipleMembersAndPrivateLinks(t *testing.T) {
	first := fixtureArchive(t, &tar.Header{Name: ".PKGINFO", Typeflag: tar.TypeReg, Size: 4})
	second := fixtureArchive(t, &tar.Header{Name: "usr/bin/tool", Typeflag: tar.TypeReg, Size: 7, Mode: 04755}, &tar.Header{Name: "bin/tool", Typeflag: tar.TypeSymlink, Linkname: "/usr/bin/tool"}, &tar.Header{Name: "dir", Typeflag: tar.TypeDir}, &tar.Header{Name: "dir-link", Typeflag: tar.TypeSymlink, Linkname: "/dir"})
	u, err := unpack(t, append(first, second...), 11)
	if err != nil {
		t.Fatal(err)
	}
	if u.remaining != 0 {
		t.Fatal("ignored metadata escaped expansion accounting")
	}
	got, err := os.ReadFile(filepath.Join(u.root, "bin/tool"))
	if err != nil || string(got) != "xxxxxxx" {
		t.Fatal(err, string(got))
	}
	info, err := os.Stat(filepath.Join(u.root, "usr/bin/tool"))
	if err != nil || info.Mode().Perm() != 0711 {
		t.Fatal("unexpected executable permissions", info, err)
	}
	if _, err := unpack(t, append(first, second...), 10); err == nil {
		t.Fatal("accepted expansion overflow")
	}
}
func TestArchiveRejectsTruncatedMember(t *testing.T) {
	data := fixtureArchive(t, &tar.Header{Name: "file", Typeflag: tar.TypeReg, Size: 10})
	if _, err := unpack(t, data[:len(data)-5], 20); err == nil {
		t.Fatal("accepted truncated gzip")
	}
}
