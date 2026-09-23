package nativeapps

import (
	"bytes"
	"compress/gzip"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func clientAssetsFixture(t *testing.T, version string) string {
	t.Helper()
	root := t.TempDir()
	index, client, err := prepareInputHTML(inputFixture(t, "index-"+version+".html"), inputFixture(t, "Client-"+version+".js"))
	if err != nil {
		t.Fatal(err)
	}
	protocol, err := prepareInputProtocol(inputFixture(t, "Protocol.js"))
	if err != nil {
		t.Fatal(err)
	}
	for name, data := range map[string][]byte{
		"index.html": index, "js/Client.js": client, "js/Protocol.js": protocol,
		"js/FloeInput.js":             []byte("/* prepared input owner */"),
		"js/DecodeWorker.js":          []byte("importScripts('RgbHelpers.js')"),
		"js/OffscreenDecodeWorker.js": []byte("importScripts('VideoDecoder.js')"),
		"css/client.css":              []byte("body {background:url('../icons/test.png')}"), "icons/test.png": []byte("image"),
		"password": []byte("never-public"), "default-settings.txt": []byte("session-only"), "launch.json": []byte("private"),
	} {
		if err := os.MkdirAll(filepath.Dir(filepath.Join(root, name)), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(root, name), data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func TestClientAssetsAreVersionedImmutableAndSessionIndependent(t *testing.T) {
	root := clientAssetsFixture(t, "v20")
	assets, err := OpenClientAssets(root)
	if err != nil {
		t.Fatal(err)
	}
	again, err := OpenClientAssets(root)
	if err != nil {
		t.Fatal(err)
	}
	if len(assets.Digest()) != 64 || assets.Digest() != again.Digest() {
		t.Fatal("identical clients must retain their version")
	}
	// A changed prepared adapter must invalidate URLs, even with the same native package.
	if err := os.WriteFile(filepath.Join(root, "js/FloeInput.js"), []byte("/* next adapter */"), 0600); err != nil {
		t.Fatal(err)
	}
	changed, err := OpenClientAssets(root)
	if err != nil {
		t.Fatal(err)
	}
	if assets.Digest() == changed.Digest() {
		t.Fatal("adapter change reused the resource version")
	}
	if err := os.RemoveAll(root); err != nil {
		t.Fatal(err)
	}
	// Existing resource snapshots are independent of application directory lifetime.
	req := httptest.NewRequest("GET", "https://host/js/Client.js", nil)
	req.Header.Set("Accept-Encoding", "br, gzip")
	res := httptest.NewRecorder()
	assets.ServeHTTP(res, req)
	if res.Code != 200 || res.Header().Get("Content-Encoding") != "gzip" || !strings.Contains(res.Header().Get("Cache-Control"), "immutable") {
		t.Fatalf("response: %d %v", res.Code, res.Header())
	}
	zr, err := gzip.NewReader(res.Body)
	if err != nil {
		t.Fatal(err)
	}
	body, err := io.ReadAll(zr)
	if err != nil {
		t.Fatal(err)
	}
	_ = zr.Close()
	if !bytes.Contains(body, []byte("XpraClient")) {
		t.Fatal("compressed client missing")
	}
	req.Header.Set("If-None-Match", res.Header().Get("ETag"))
	cached := httptest.NewRecorder()
	assets.ServeHTTP(cached, req)
	if cached.Code != 304 || cached.Body.Len() != 0 {
		t.Fatalf("conditional cache response: %d", cached.Code)
	}
	req = httptest.NewRequest("HEAD", "https://host/js/Client.js", nil)
	res = httptest.NewRecorder()
	assets.ServeHTTP(res, req)
	if res.Code != 200 || res.Body.Len() != 0 || res.Header().Get("Content-Length") == "" {
		t.Fatal("HEAD must return metadata without content")
	}
}

func TestClientAssetsKeepDocumentsAndPrivateFilesOutOfCache(t *testing.T) {
	assets, err := OpenClientAssets(clientAssetsFixture(t, "v20"))
	if err != nil {
		t.Fatal(err)
	}
	for _, p := range []string{"/index.html", "/password", "/launch.json", "/default-settings.txt", "/js/../password", "//js/Client.js", "/js/%2e%2e/password", "/js/missing.js"} {
		req := httptest.NewRequest("GET", "https://host"+p, nil)
		res := httptest.NewRecorder()
		assets.ServeHTTP(res, req)
		if res.Code != 404 || res.Header().Get("Cache-Control") != "no-store" {
			t.Fatalf("unsafe asset %s: %d %v", p, res.Code, res.Header())
		}
	}
	req := httptest.NewRequest("POST", "https://host/js/Client.js", nil)
	res := httptest.NewRecorder()
	assets.ServeHTTP(res, req)
	if res.Code != 405 || res.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("asset mutation accepted")
	}
	for _, accept := range []string{"gzip;q=0", "gzip;q=0, *;q=1", "xgzip", "br"} {
		req := httptest.NewRequest("GET", "https://host/js/Client.js", nil)
		req.Header.Set("Accept-Encoding", accept)
		res := httptest.NewRecorder()
		assets.ServeHTTP(res, req)
		if res.Header().Get("Content-Encoding") != "" {
			t.Fatalf("unsupported gzip negotiation %q", accept)
		}
	}
}

func TestClientAssetsRewriteOnlyPublicReferencesAndKeepWorkersWithScripts(t *testing.T) {
	for _, version := range []string{"v20", "v21"} {
		t.Run(version, func(t *testing.T) {
			root := clientAssetsFixture(t, version)
			assets, err := OpenClientAssets(root)
			if err != nil {
				t.Fatal(err)
			}
			prefix := "/_host/client/" + assets.Digest() + "/"
			doc := []byte(`<script src="js/Client.js"></script><link href="css/client.css"><img src="icons/test.png"><script src="session.js"></script><a href="https://other.test/js/Client.js">outside</a>`)
			html, err := assets.RewriteHTML(doc, prefix)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Contains(html, []byte(`src="`+prefix+`js/Client.js"`)) || !bytes.Contains(html, []byte(`href="`+prefix+`css/client.css"`)) || !bytes.Contains(html, []byte(`src="session.js"`)) || !bytes.Contains(html, []byte(`href="https://other.test/js/Client.js"`)) {
				t.Fatalf("rewritten document: %s", html)
			}
			for _, invalid := range []string{"//other.test/", "https://other.test/", "/a/../b/", "/a?b/", "/a\"/", "/a%2fb/"} {
				if _, err := assets.RewriteHTML(doc, invalid); err == nil {
					t.Fatalf("unsafe asset base accepted: %s", invalid)
				}
			}
			// Execute the real prepared vendor classes with delayed worker creation.
			dir := t.TempDir()
			for _, name := range []string{"Client.js", "Protocol.js"} {
				res := httptest.NewRecorder()
				assets.ServeHTTP(res, httptest.NewRequest("GET", "https://host/js/"+name, nil))
				if err := os.WriteFile(filepath.Join(dir, name), res.Body.Bytes(), 0600); err != nil {
					t.Fatal(err)
				}
			}
			cmd := exec.Command("node", "--test", "client_assets_test.cjs")
			cmd.Env = append(os.Environ(), "FLOE_ASSETS_FIXTURE="+dir)
			if output, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("worker URL execution: %v\n%s", err, output)
			}
		})
	}
}

func TestClientAssetsRejectEscapingSymlinksAndUnknownWorkerContracts(t *testing.T) {
	root := clientAssetsFixture(t, "v20")
	outside := filepath.Join(t.TempDir(), "secret.js")
	if err := os.WriteFile(outside, []byte("private"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(root, "js", "external.js")); err != nil {
		t.Skip(err)
	}
	if _, err := OpenClientAssets(root); err == nil {
		t.Fatal("escaping asset symlink accepted")
	}
	if err := os.Remove(filepath.Join(root, "js", "external.js")); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "js/Client.js"), []byte("new Worker('unreviewed.js')"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := OpenClientAssets(root); err == nil {
		t.Fatal("unknown worker contract accepted")
	}
}

func TestClientAssetsRejectOversizedFilesBeforeReading(t *testing.T) {
	root := clientAssetsFixture(t, "v20")
	f, err := os.Create(filepath.Join(root, "js", "oversized.js"))
	if err != nil {
		t.Fatal(err)
	}
	if err := f.Truncate((32 << 20) + 1); err != nil {
		t.Fatal(err)
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := OpenClientAssets(root); err == nil {
		t.Fatal("oversized asset accepted")
	}
}

var _ http.Handler = (*ClientAssets)(nil)
