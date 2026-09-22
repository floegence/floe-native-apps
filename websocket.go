package nativeapps

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
)

// The pinned Xpra 6.2.2 mask extension writes its alignment prefix even when
// the payload is shorter than that prefix. Keep small frames in the Python
// decoder; normal graphics packets retain the native mask implementation.
// Remove this preparation patch when the catalog adopts a publisher fix, after
// the short-frame and disconnect/reconnect qualification passes unchanged.
func prepareXpraWebSocket(root string) error {
	path := filepath.Join(root, "usr/lib/python3.12/site-packages/xpra/net/websockets/header.py")
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	const original = "    if masked:\n        payload = hybi_unmask(buf, hlen - 4, payload_len)\n"
	const corrected = `    if masked:
        # Floe Native Apps: the pinned native mask function overflows its
        # allocation for payloads shorter than its alignment prefix.
        if payload_len < 4:
            mask = buf[hlen - 4:hlen]
            payload = bytes(value ^ mask[i & 3] for i, value in enumerate(buf[hlen:length]))
        else:
            payload = hybi_unmask(buf, hlen - 4, payload_len)
`
	if bytes.Count(data, []byte(original)) != 1 {
		return errors.New("unreviewed native WebSocket decoder")
	}
	if err = os.WriteFile(path, bytes.Replace(data, []byte(original), []byte(corrected), 1), 0600); err != nil {
		return err
	}
	// Publisher hash-based bytecode can otherwise ignore the corrected source.
	caches, err := filepath.Glob(filepath.Join(filepath.Dir(path), "__pycache__/header.*.pyc"))
	if err != nil {
		return err
	}
	for _, cache := range caches {
		if err = os.Remove(cache); err != nil {
			return err
		}
	}
	return nil
}
