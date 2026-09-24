package nativeapps

import (
	"bytes"
	"crypto/sha256"
	"debug/elf"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestInputModuleProvenance(t *testing.T) {
	for _, arch := range []string{"amd64", "arm64"} {
		for _, major := range []string{"5", "6"} {
			t.Run(arch+"-qt"+major, func(t *testing.T) {
				root := filepath.Join("input_modules", "dist", arch)
				data, err := os.ReadFile(filepath.Join(root, "build-qt"+major+".json"))
				if err != nil {
					t.Fatal(err)
				}
				var record struct {
					Builder   string            `json:"builder_image"`
					Sources   map[string]string `json:"source_sha256"`
					Artifacts map[string]string `json:"artifacts"`
					Packages  []string          `json:"packages"`
				}
				if err := json.Unmarshal(data, &record); err != nil {
					t.Fatal(err)
				}
				if !strings.HasPrefix(record.Builder, "public.ecr.aws/docker/library/debian@sha256:") || len(record.Packages) < 20 || len(record.Sources) < 3 {
					t.Fatal("incomplete native build provenance")
				}
				if major == "5" {
					for _, name := range []string{"libfloe-gtk3.so", "libfloe-gtk4.so", "libfloe-qt5.so"} {
						if record.Artifacts[name] == "" {
							t.Fatalf("missing native build artifact: %s", name)
						}
					}
				}
				for path, expected := range record.Sources {
					contents, err := os.ReadFile(path)
					if err != nil {
						t.Fatal(err)
					}
					digest := sha256.Sum256(contents)
					if hex.EncodeToString(digest[:]) != expected {
						t.Fatalf("%s requires a native module rebuild", path)
					}
				}
				for name, expected := range record.Artifacts {
					contents, err := os.ReadFile(filepath.Join(root, name))
					if err != nil {
						t.Fatal(err)
					}
					digest := sha256.Sum256(contents)
					if hex.EncodeToString(digest[:]) != expected {
						t.Fatalf("%s does not match its native build", name)
					}
					binary, err := elf.NewFile(bytes.NewReader(contents))
					if err != nil {
						t.Fatal(err)
					}
					machine := elf.EM_X86_64
					if arch == "arm64" {
						machine = elf.EM_AARCH64
					}
					for _, tag := range []elf.DynTag{elf.DT_RPATH, elf.DT_RUNPATH} {
						paths, err := binary.DynString(tag)
						if err != nil || len(paths) != 0 {
							t.Fatalf("%s leaks a build-time loader path: %v, %v", name, paths, err)
						}
					}
					if name == "libfloe-gtk4.so" {
						libraries, err := binary.ImportedLibraries()
						if err != nil || !strings.Contains(strings.Join(libraries, "\n"), "libgtk-4.so.1") || strings.Contains(strings.Join(libraries, "\n"), "libgtk-3") {
							t.Fatalf("GTK4 adapter has the wrong toolkit ABI: %v, %v", libraries, err)
						}
					}
					if binary.Machine != machine || binary.Type != elf.ET_DYN {
						t.Fatal("incorrect native module target")
					}
				}
			})
		}
	}
}
