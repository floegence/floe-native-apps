package nativeapps

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

// ClientAssets is an immutable snapshot of public resources from a directory
// produced by PrepareInputClient. It excludes HTML, configuration and session
// files. Hosts own authorization, route placement and snapshot lifetime.
type ClientAssets struct {
	digest string
	files  map[string]clientAsset
}

type clientAsset struct {
	data, gzip                  []byte
	contentType, etag, gzipETag string
}

// OpenClientAssets snapshots and precompresses a prepared Xpra HTML5 v20/v21
// client. Worker URLs follow the script's resource directory, independently of
// the session document URL. The source and installed vendor files are unchanged.
// The digest covers the exact prepared resource names and bytes, including the
// input adapter and worker transformation, rather than only the native recipe.
func OpenClientAssets(directory string) (*ClientAssets, error) {
	if !filepath.IsAbs(directory) {
		return nil, ErrInvalid
	}
	info, err := os.Lstat(directory)
	if err != nil {
		return nil, err
	}
	if !info.IsDir() {
		return nil, ErrInvalid
	}
	root, err := os.OpenRoot(directory)
	if err != nil {
		return nil, err
	}
	defer root.Close()
	a := &ClientAssets{files: make(map[string]clientAsset)}
	var total int64
	err = fs.WalkDir(root.FS(), ".", func(name string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			if name == "." || name == "js" || name == "css" || name == "icons" || name == "fonts" || strings.HasPrefix(name, "js/") || strings.HasPrefix(name, "css/") || strings.HasPrefix(name, "icons/") || strings.HasPrefix(name, "fonts/") {
				return nil
			}
			return fs.SkipDir
		}
		contentType := clientAssetContentType(name)
		if contentType == "" {
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		total += info.Size()
		if !info.Mode().IsRegular() || info.Size() > 32<<20 || total > 128<<20 || len(a.files) >= 4096 {
			return errors.New("invalid or oversized client assets")
		}
		data, err := root.ReadFile(name)
		if err != nil {
			return err
		}
		switch name {
		case "js/Client.js":
			data, err = clientWorkerURLs(data, "FLOE_CLIENT_SCRIPT_URL", []string{"OffscreenDecodeWorker.js", "DecodeWorker.js"})
		case "js/Protocol.js":
			data, err = clientWorkerURLs(data, "FLOE_PROTOCOL_SCRIPT_URL", []string{"Protocol.js"})
		}
		if err != nil {
			return err
		}
		asset := clientAsset{data: data, contentType: contentType, etag: clientAssetETag(data)}
		var compressed bytes.Buffer
		z, _ := gzip.NewWriterLevel(&compressed, gzip.BestCompression)
		if _, err = z.Write(data); err != nil {
			return err
		}
		if err = z.Close(); err != nil {
			return err
		}
		if compressed.Len() < len(data) {
			asset.gzip = compressed.Bytes()
			asset.gzipETag = clientAssetETag(asset.gzip)
		}
		a.files[name] = asset
		return nil
	})
	if err != nil {
		return nil, err
	}
	for _, name := range []string{"js/Client.js", "js/Protocol.js", "js/FloeInput.js", "js/DecodeWorker.js", "js/OffscreenDecodeWorker.js"} {
		if _, ok := a.files[name]; !ok {
			return nil, fmt.Errorf("incomplete prepared client: %s", name)
		}
	}
	names := make([]string, 0, len(a.files))
	for name := range a.files {
		names = append(names, name)
	}
	sort.Strings(names)
	h := sha256.New()
	for _, name := range names {
		data := a.files[name].data
		fmt.Fprintf(h, "%s\x00%d\x00", name, len(data))
		_, _ = h.Write(data)
	}
	a.digest = hex.EncodeToString(h.Sum(nil))
	return a, nil
}

func clientWorkerURLs(data []byte, identifier string, workers []string) ([]byte, error) {
	source := string(data)
	for _, worker := range workers {
		old := `new Worker("js/` + worker + `")`
		if strings.Count(source, old) != 1 {
			return nil, errors.New("unsupported Xpra client worker contract")
		}
		source = strings.Replace(source, old, `new Worker(new URL("`+worker+`", `+identifier+`))`, 1)
	}
	// Capture during script evaluation; document.currentScript is null when an
	// asynchronous connection later creates workers. Protocol.js also executes
	// inside its own worker, where relative importScripts must remain intact.
	source += "\nconst " + identifier + " = typeof document === 'undefined' ? null : document.currentScript.src;\n"
	return []byte(source), nil
}

func clientAssetContentType(name string) string {
	if name != "favicon.png" && !strings.HasPrefix(name, "js/") && !strings.HasPrefix(name, "css/") && !strings.HasPrefix(name, "icons/") && !strings.HasPrefix(name, "fonts/") {
		return ""
	}
	return map[string]string{".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".wasm": "application/wasm", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".svg": "image/svg+xml", ".ico": "image/x-icon", ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf"}[path.Ext(name)]
}

func clientAssetETag(data []byte) string {
	h := sha256.Sum256(data)
	return `"` + hex.EncodeToString(h[:]) + `"`
}

// Digest is the content version to include in the host's resource route.
func (a *ClientAssets) Digest() string { return a.digest }

var clientAssetReference = regexp.MustCompile(`((?:src|href)=["'])([^"'<>]+)(["'])`)

// RewriteHTML relocates only references to resources in this snapshot. basePath
// must be an absolute same-origin directory path containing the content version.
// It leaves WebSocket addresses, session settings and inline startup code alone.
// The resulting session document must remain authenticated and uncached.
func (a *ClientAssets) RewriteHTML(document []byte, basePath string) ([]byte, error) {
	if !strings.HasPrefix(basePath, "/") || !strings.HasSuffix(basePath, "/") || strings.HasPrefix(basePath, "//") || strings.ContainsAny(basePath, "%?#\\\"'<>\r\n\t ") || path.Clean(basePath)+"/" != basePath || !strings.Contains(basePath, "/"+a.digest+"/") {
		return nil, ErrInvalid
	}
	return clientAssetReference.ReplaceAllFunc(document, func(match []byte) []byte {
		parts := clientAssetReference.FindSubmatch(match)
		ref, err := url.Parse(string(parts[2]))
		if err != nil || ref.IsAbs() || ref.Host != "" || strings.HasPrefix(ref.Path, "/") {
			return match
		}
		name := strings.TrimPrefix(ref.Path, "./")
		if _, ok := a.files[name]; !ok {
			return match
		}
		ref.Path = basePath + name
		ref.RawPath = ""
		return []byte(string(parts[1]) + ref.String() + string(parts[3]))
	}), nil
}

// ServeHTTP serves a resource path (for example /js/Client.js). The host must
// first select this snapshot by its exact digest and strip the versioned prefix.
// Authorization belongs before this handler. Cached scripts never authorize a
// connection: hosts must continue authenticating session documents and traffic.
func (a *ClientAssets) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		w.Header().Set("Allow", "GET, HEAD")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	name := strings.TrimPrefix(r.URL.Path, "/")
	asset, ok := a.files[name]
	if !ok || r.URL.Path != "/"+name || !fs.ValidPath(name) {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", asset.contentType)
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Cache-Control", "private, max-age=31536000, immutable")
	w.Header().Set("Vary", "Accept-Encoding")
	data, etag := asset.data, asset.etag
	if len(asset.gzip) > 0 && acceptsClientGzip(r.Header.Get("Accept-Encoding")) {
		data, etag = asset.gzip, asset.gzipETag
		w.Header().Set("Content-Encoding", "gzip")
	}
	w.Header().Set("ETag", etag)
	http.ServeContent(w, r, path.Base(name), time.Time{}, bytes.NewReader(data))
}

func acceptsClientGzip(header string) bool {
	wildcard := false
	for _, item := range strings.Split(header, ",") {
		fields := strings.Split(item, ";")
		coding := strings.TrimSpace(strings.ToLower(fields[0]))
		if coding != "gzip" && coding != "*" {
			continue
		}
		q := 1.0
		for _, field := range fields[1:] {
			key, value, ok := strings.Cut(strings.TrimSpace(field), "=")
			if ok && strings.EqualFold(key, "q") {
				parsed, err := strconv.ParseFloat(value, 64)
				if err != nil || parsed < 0 || parsed > 1 {
					q = 0
				} else {
					q = parsed
				}
			}
		}
		if coding == "gzip" {
			return q > 0
		}
		wildcard = q > 0
	}
	return wildcard
}
