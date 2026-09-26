package nativeapps

import (
	"fmt"
	"net/url"
	"os"
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
	return v.assets.RewriteHTML(v.document, basePath)
}
