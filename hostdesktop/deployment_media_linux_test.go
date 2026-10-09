//go:build linux

package hostdesktop

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestLoginMediaExtractionRejectsLinksTraversalAndDuplicateFiles(t *testing.T) {
	for _, test := range []struct {
		name      string
		kind      byte
		duplicate bool
	}{
		{"../escape", tar.TypeReg, false}, {"/absolute", tar.TypeReg, false},
		{"media", tar.TypeSymlink, false}, {"media", tar.TypeLink, false},
		{"media", tar.TypeChar, false}, {"media", tar.TypeReg, true},
	} {
		var data bytes.Buffer
		gzip := gzip.NewWriter(&data)
		archive := tar.NewWriter(gzip)
		count := 1
		if test.duplicate {
			count = 2
		}
		for range count {
			header := &tar.Header{Name: test.name, Typeflag: test.kind, Mode: 0755, Linkname: "/escape"}
			if test.kind == tar.TypeReg {
				header.Size = 1
			}
			if err := archive.WriteHeader(header); err != nil {
				t.Fatal(err)
			}
			if header.Size != 0 {
				if _, err := archive.Write([]byte("x")); err != nil {
					t.Fatal(err)
				}
			}
		}
		if archive.Close() != nil || gzip.Close() != nil {
			t.Fatal("archive fixture")
		}
		base := t.TempDir()
		source := filepath.Join(base, "media.tar.gz")
		if os.WriteFile(source, data.Bytes(), 0600) != nil {
			t.Fatal("archive fixture write")
		}
		if loginExtractMedia(t.Context(), source, filepath.Join(base, "output")) == nil {
			t.Fatalf("accepted %q type %d", test.name, test.kind)
		}
	}
}

func TestLoginMediaUpgradeRestoresExactVersionOneRecord(t *testing.T) {
	d, request, systemd, _ := fixtureLoginDeployment(t)
	if err := d.manage(t.Context(), request); err != nil {
		t.Fatal(err)
	}
	record, err := d.readRecord()
	if err != nil {
		t.Fatal(err)
	}
	currentDigest := record.Digest
	record.Version, record.Request.MediaSHA256 = 1, ""
	policy, _ := json.Marshal(record.Request)
	hash := sha256.Sum256(policy)
	record.Digest = hex.EncodeToString(hash[:])
	oldDirectory := filepath.Join(d.root, "versions", record.Digest)
	if err := os.Rename(filepath.Join(d.root, "versions", currentDigest), oldDirectory); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(d.unit, d.unitContents(oldDirectory, record.Request), 0644); err != nil {
		t.Fatal(err)
	}
	data, _ := json.MarshalIndent(record, "", "  ")
	data = append(data, '\n')
	if err := os.WriteFile(filepath.Join(d.root, "installed.json"), data, 0600); err != nil {
		t.Fatal(err)
	}
	old, err := d.readRecord()
	if err != nil || old.Version != 1 {
		t.Fatal("old policy lineage rejected", err)
	}
	request.Operation = "update"
	systemd.fail = "start"
	if d.manage(context.Background(), request) == nil {
		t.Fatal("failed media update accepted")
	}
	restored, err := os.ReadFile(filepath.Join(d.root, "installed.json"))
	if err != nil || !bytes.Equal(data, restored) || !systemd.active {
		t.Fatal("upgrade rollback altered v1 bytes or running state", err)
	}
	if err := d.manage(t.Context(), request); err != nil {
		t.Fatal(err)
	}
	updated, err := d.readRecord()
	if err != nil || updated.Version != 2 || updated.Request.MediaSHA256 == "" {
		t.Fatal("media upgrade did not preserve explicit lineage", err)
	}
}
