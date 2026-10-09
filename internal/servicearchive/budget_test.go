package servicearchive

import (
	"archive/tar"
	"bytes"
	"testing"
)

func TestResolvedClosureAndExpandedLimit(t *testing.T) {
	var budget Budget
	header := &tar.Header{Name: "usr/lib/qualified-library", Mode: 0755, Typeflag: tar.TypeReg, Size: MaxFileBytes}
	for range MaxExpandedBytes / MaxFileBytes {
		if err := budget.Accept(header); err != nil {
			t.Fatal("rejected bounded resolved closure", err)
		}
	}
	header.Size = 1
	if budget.Accept(header) == nil {
		t.Fatal("accepted archive above expanded limit")
	}
}

func TestBoundedWriterRejectsBeforeExceedingCompressedLimit(t *testing.T) {
	var output bytes.Buffer
	writer := NewBoundedWriter(&output, 3)
	if n, err := writer.Write([]byte("abc")); n != 3 || err != nil {
		t.Fatal(n, err)
	}
	if _, err := writer.Write([]byte("d")); err == nil {
		t.Fatal("writer exceeded limit")
	}
	if output.String() != "abc" {
		t.Fatal(output.String())
	}
}

func TestArchiveBudgetRejectsUnsafeHeadersAndEntryOverflow(t *testing.T) {
	for _, header := range []*tar.Header{
		{Name: "../escape", Mode: 0644, Typeflag: tar.TypeReg},
		{Name: "/absolute", Mode: 0644, Typeflag: tar.TypeReg},
		{Name: "a/../b", Mode: 0644, Typeflag: tar.TypeReg},
		{Name: "a\\b", Mode: 0644, Typeflag: tar.TypeReg},
		{Name: "link", Mode: 0644, Typeflag: tar.TypeLink},
		{Name: "link", Mode: 0644, Typeflag: tar.TypeSymlink},
		{Name: "device", Mode: 0644, Typeflag: tar.TypeChar},
		{Name: "file", Mode: 04755, Typeflag: tar.TypeReg},
		{Name: "file", Mode: 0644, Typeflag: tar.TypeReg, Size: -1},
		{Name: "file", Mode: 0644, Typeflag: tar.TypeReg, Size: MaxFileBytes + 1},
	} {
		var budget Budget
		if budget.Accept(header) == nil || budget.bytes != 0 || budget.entries != 0 {
			t.Fatalf("accepted unsafe header: %+v", header)
		}
	}
	var budget Budget
	header := &tar.Header{Name: "empty", Mode: 0644, Typeflag: tar.TypeReg}
	for range MaxEntries {
		if err := budget.Accept(header); err != nil {
			t.Fatal(err)
		}
	}
	if budget.Accept(header) == nil {
		t.Fatal("accepted excessive entries")
	}
}
