package nativeapps

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestWebSocketPreparationRejectsSourceDrift(t *testing.T) {
	const original = "    if masked:\n        payload = hybi_unmask(buf, hlen - 4, payload_len)\n"
	for _, content := range []string{"changed publisher source", original + original} {
		root := t.TempDir()
		path := filepath.Join(root, "usr/lib/python3.12/site-packages/xpra/net/websockets/header.py")
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(content), 0600); err != nil {
			t.Fatal(err)
		}
		if err := prepareXpraWebSocket(root); err == nil {
			t.Fatal("unreviewed source was accepted")
		}
		got, _ := os.ReadFile(path)
		if string(got) != content {
			t.Fatal("rejected source was modified")
		}
	}
}

func TestWebSocketPreparationInvalidatesOnlyDecoderBytecode(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "usr/lib/python3.12/site-packages/xpra/net/websockets")
	cache := filepath.Join(dir, "__pycache__")
	if err := os.MkdirAll(cache, 0700); err != nil {
		t.Fatal(err)
	}
	for name, data := range map[string]string{
		"header.py":                                "# Publisher license retained\n    if masked:\n        payload = hybi_unmask(buf, hlen - 4, payload_len)\n    else:\n        payload = buf[hlen:length]\n",
		"__pycache__/header.cpython-312.pyc":       "stale hash-based bytecode",
		"__pycache__/header.cpython-312.opt-1.pyc": "stale optimized bytecode",
		"__pycache__/protocol.cpython-312.pyc":     "unrelated bytecode",
	} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(data), 0600); err != nil {
			t.Fatal(err)
		}
	}
	if err := prepareXpraWebSocket(root); err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(filepath.Join(dir, "header.py"))
	if !strings.HasPrefix(string(data), "# Publisher license retained\n") || !strings.Contains(string(data), "else:\n        payload = buf[hlen:length]") {
		t.Fatal("preparation changed unrelated publisher source")
	}
	entries, _ := os.ReadDir(cache)
	if len(entries) != 1 || entries[0].Name() != "protocol.cpython-312.pyc" {
		t.Fatal("decoder caches were not invalidated independently")
	}
}
