package nativeapps

import (
	"bytes"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func originalViewerFixture(t *testing.T, version string) string {
	t.Helper()
	root := t.TempDir()
	index := inputFixture(t, "index-"+version+".html")
	files := map[string][]byte{"index.html": index, "js/Client.js": inputFixture(t, "Client-"+version+".js"), "js/Window.js": inputFixture(t, "Window-"+version+".js"), "js/Protocol.js": inputFixture(t, "Protocol.js"), "js/DecodeWorker.js": []byte("/* worker */"), "js/OffscreenDecodeWorker.js": inputFixture(t, "OffscreenDecodeWorker.js")}
	for _, m := range clientAssetReference.FindAllSubmatch(index, -1) {
		name := string(m[2])
		if clientAssetContentType(name) != "" {
			if _, ok := files[name]; !ok {
				files[name] = []byte("/* fixture resource */")
			}
		}
	}
	for name, data := range files {
		p := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(p), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func TestPreparedViewerPinsMatchingCurrentDocumentAndAssets(t *testing.T) {
	for _, version := range []string{"v20", "v21"} {
		t.Run(version, func(t *testing.T) {
			root := originalViewerFixture(t, version)
			viewer, err := PrepareViewer(root)
			if err != nil {
				t.Fatal(err)
			}
			base := "/assets/" + viewer.Assets().Digest() + "/"
			document, err := viewer.Document(base)
			if err != nil {
				t.Fatal(err)
			}
			for _, name := range []string{"FloeViewer", "FloeLayout", "FloeInput", "FloeDisplay", "FloePointer"} {
				if !bytes.Contains(document, []byte(base+"js/"+name+".js")) {
					t.Fatalf("missing current %s", name)
				}
			}
			if err := os.WriteFile(filepath.Join(root, "index.html"), []byte("old or corrupt document"), 0600); err != nil {
				t.Fatal(err)
			}
			if _, err := PrepareViewer(root); err == nil {
				t.Fatal("corrupt source accepted")
			}
			document[0] = '!'
			again, err := viewer.Document(base)
			if err != nil || again[0] == '!' {
				t.Fatal("mutable document")
			}
			if err := os.RemoveAll(root); err != nil {
				t.Fatal(err)
			}
			res := httptest.NewRecorder()
			viewer.Assets().ServeHTTP(res, httptest.NewRequest("GET", "/js/FloeViewer.js", nil))
			if res.Code != 200 || !strings.Contains(res.Body.String(), "capabilities") {
				t.Fatal("snapshot did not survive source lifetime")
			}
		})
	}
}
func TestPreparedViewerRejectsMissingDocumentResource(t *testing.T) {
	root := originalViewerFixture(t, "v20")
	if err := os.Remove(filepath.Join(root, "js/Keycodes.js")); err != nil {
		t.Fatal(err)
	}
	if _, err := PrepareViewer(root); err == nil {
		t.Fatal("incomplete viewer accepted")
	}
}
