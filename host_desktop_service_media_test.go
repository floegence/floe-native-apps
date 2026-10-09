package nativeapps

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"io"
	"os"
	"path/filepath"
	"testing"

	"github.com/floegence/floe-native-apps/internal/servicearchive"
)

func TestLoginScreenMediaResolvesAliasesToRegularFiles(t *testing.T) {
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "library"), []byte("verified library"), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink("library", filepath.Join(root, "alias")); err != nil {
		t.Fatal(err)
	}
	output := filepath.Join(t.TempDir(), "media.tar.gz")
	if _, err := writeLoginScreenMedia(t.Context(), root, output); err != nil {
		t.Fatal(err)
	}
	file, err := os.Open(output)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	compressed, err := gzip.NewReader(file)
	if err != nil {
		t.Fatal(err)
	}
	defer compressed.Close()
	archive := tar.NewReader(compressed)
	var budget servicearchive.Budget
	seen := map[string]bool{}
	for {
		header, err := archive.Next()
		if err == io.EOF {
			break
		}
		if err != nil || budget.Accept(header) != nil {
			t.Fatal("producer violated installer contract", err)
		}
		data, err := io.ReadAll(archive)
		if err != nil || string(data) != "verified library" || header.Mode != 0755 {
			t.Fatal("alias content or mode changed", err)
		}
		seen[header.Name] = true
	}
	if !seen["alias"] || !seen["library"] || len(seen) != 2 {
		t.Fatal(seen)
	}
}

func TestLoginScreenMediaRemovesCanceledAndOversizedOutput(t *testing.T) {
	for _, canceled := range []bool{false, true} {
		root := t.TempDir()
		file, err := os.Create(filepath.Join(root, "oversized"))
		if err != nil {
			t.Fatal(err)
		}
		err = file.Truncate(servicearchive.MaxFileBytes + 1)
		closeErr := file.Close()
		if err != nil || closeErr != nil {
			t.Fatal(err, closeErr)
		}
		ctx, cancel := context.WithCancel(t.Context())
		defer cancel()
		if canceled {
			cancel()
		}
		output := filepath.Join(t.TempDir(), "media.tar.gz")
		if _, err := writeLoginScreenMedia(ctx, root, output); err == nil {
			t.Fatal("invalid export accepted")
		}
		if _, err := os.Stat(output); !os.IsNotExist(err) {
			t.Fatal("partial export retained", err)
		}
	}
}
