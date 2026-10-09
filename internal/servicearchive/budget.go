package servicearchive

import (
	"archive/tar"
	"errors"
	"io"
	"path"
	"path/filepath"
	"strings"
)

const MaxCompressedBytes int64 = 512 << 20
const MaxFileBytes int64 = 256 << 20

// Resolved library aliases in the qualified media closure exceed one GiB.
const MaxExpandedBytes int64 = 2 << 30
const MaxEntries = 100000

var ErrInvalid = errors.New("invalid service media archive")

type BoundedWriter struct {
	Writer    io.Writer
	remaining int64
}

func NewBoundedWriter(writer io.Writer, limit int64) *BoundedWriter {
	return &BoundedWriter{Writer: writer, remaining: limit}
}

func (w *BoundedWriter) Write(data []byte) (int, error) {
	if int64(len(data)) > w.remaining {
		n, err := w.Writer.Write(data[:w.remaining])
		w.remaining -= int64(n)
		if err != nil {
			return n, err
		}
		return n, ErrInvalid
	}
	n, err := w.Writer.Write(data)
	w.remaining -= int64(n)
	return n, err
}

// Budget is shared by the unprivileged producer and privileged extractor.
// Its zero value starts a new archive; rejected entries do not consume it.
type Budget struct {
	bytes   int64
	entries int
}

func (b *Budget) Accept(header *tar.Header) error {
	if b.entries >= MaxEntries || header.Typeflag != tar.TypeReg || header.Size < 0 ||
		header.Size > MaxFileBytes || header.Size > MaxExpandedBytes-b.bytes ||
		!filepath.IsLocal(header.Name) || path.Clean(header.Name) != header.Name ||
		strings.Contains(header.Name, "\\") || (header.Mode != 0644 && header.Mode != 0755) {
		return ErrInvalid
	}
	b.bytes += header.Size
	b.entries++
	return nil
}
