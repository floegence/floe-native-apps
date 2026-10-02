package nativeapps

import (
	"fmt"
	"html"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"strings"
)

// PreparedViewer is an immutable, matching document and resource snapshot made
// by this SDK from an original reviewed HTML5 distribution. It owns no backend,
// input module or application process. A host pins it for one sharing session.
type PreparedViewer struct {
	document []byte
	assets   *ClientAssets
}

// PrepareViewer uses the same preparation as PrepareInputClient, independently
// of any application's old prepared directory. Failure never yields an older
// viewer. The source must be a trusted installed original v20/v21 distribution.
func PrepareViewer(source string) (*PreparedViewer, error) {
	if !filepath.IsAbs(source) {
		return nil, ErrInvalid
	}
	temp, err := os.MkdirTemp("", "floe-viewer-")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(temp)
	prepared := filepath.Join(temp, "client")
	if err = PrepareInputClient(source, prepared); err != nil {
		return nil, fmt.Errorf("prepare viewer: %w", err)
	}
	assets, err := OpenClientAssets(prepared)
	if err != nil {
		return nil, fmt.Errorf("prepare viewer assets: %w", err)
	}
	document, err := os.ReadFile(filepath.Join(prepared, "index.html"))
	if err != nil {
		return nil, err
	}
	if len(document) > 2<<20 {
		return nil, fmt.Errorf("viewer document exceeds size limit")
	}
	// Every public reference in the prepared entry point must belong to this
	// snapshot. Session endpoints and document links stay outside the asset set.
	for _, match := range clientAssetReference.FindAllSubmatch(document, -1) {
		ref, err := url.Parse(string(match[2]))
		if err != nil {
			return nil, err
		}
		name := strings.TrimPrefix(ref.Path, "./")
		if ref.IsAbs() || ref.Host != "" || clientAssetContentType(name) == "" {
			continue
		}
		if _, ok := assets.files[name]; !ok {
			return nil, fmt.Errorf("incomplete viewer resource: %s", name)
		}
	}
	return &PreparedViewer{document: document, assets: assets}, nil
}

// Assets returns the immutable public resources. Hosts must authorize each
// request by owner, active sharing origin and exact content digest.
func (v *PreparedViewer) Assets() *ClientAssets { return v.assets }

// Document returns this snapshot's entry point with versioned public references.
// The document, settings, credentials and transport must never be cached.
func (v *PreparedViewer) Document(basePath string) ([]byte, error) {
	return v.DocumentWithOptions(basePath, ViewerDocumentOptions{})
}

// ViewerDocumentOptions binds a prepared client to a host-owned transport.
// The same-origin script must synchronously publish floeHostTransport.WebSocket,
// a WebSocket-compatible constructor. Missing transport fails closed. Protocol
// processing stays in this realm; independent graphics decode workers remain enabled.
// The host owns authorization, carrier lifetime and script CSP permission.
type ViewerDocumentOptions struct {
	TransportScriptURL string
}

func (v *PreparedViewer) DocumentWithOptions(basePath string, options ViewerDocumentOptions) ([]byte, error) {
	document, err := v.assets.RewriteHTML(v.document, basePath)
	if err != nil || options.TransportScriptURL == "" {
		return document, err
	}
	u, err := url.Parse(options.TransportScriptURL)
	if err != nil || u.IsAbs() || u.Host != "" || !strings.HasPrefix(u.Path, "/") ||
		u.Path != path.Clean(u.Path) || u.RawQuery != "" || u.Fragment != "" ||
		u.RawPath != "" || strings.ContainsAny(options.TransportScriptURL, "%?#\\\"'<>\r\n\t ") {
		return nil, ErrInvalid
	}
	source := string(document)
	if strings.Count(source, "<html") != 1 || strings.Count(source, "<head>") != 1 {
		return nil, ErrInvalid
	}
	source = strings.Replace(source, "<html", `<html data-floe-host-transport="required"`, 1)
	source = strings.Replace(source, "<head>", `<head><script src="`+html.EscapeString(u.Path)+`"></script>`, 1)
	return []byte(source), nil
}
