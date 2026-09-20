// Package nativeapps prepares a private native graphical stack without changing
// system packages. Callers own user consent, authorization, and application launch.
package nativeapps

import (
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"path/filepath"
	"runtime"
)

type Artifact struct {
	Name    string `json:"name"`
	URL     string `json:"url"`
	SHA256  string `json:"sha256"`
	Size    int64  `json:"size_bytes"`
	Format  string `json:"format"`
	License string `json:"license"`
	Source  string `json:"source"`
}

type Package struct {
	ID             string     `json:"id"`
	Architecture   string     `json:"architecture"`
	SizeBytes      int64      `json:"size_bytes"`
	InstalledBytes int64      `json:"installed_bytes"`
	Artifacts      []Artifact `json:"artifacts"`
}

//go:embed catalog.json
var catalog embed.FS

func ForPlatform(platform, architecture string) (Package, error) {
	if platform != "linux" {
		return Package{}, ErrUnsupported
	}
	var packages []Package
	data, err := catalog.ReadFile("catalog.json")
	if err != nil {
		return Package{}, err
	}
	if err = json.Unmarshal(data, &packages); err != nil {
		return Package{}, err
	}
	for _, p := range packages {
		if p.Architecture == architecture {
			return p, p.Validate()
		}
	}
	return Package{}, ErrUnsupported
}

func NativePackage() (Package, error) { return ForPlatform(runtime.GOOS, runtime.GOARCH) }

var ErrUnsupported = errors.New("native graphical components are unavailable for this platform")

func (p Package) Validate() error {
	if !filepath.IsLocal(p.ID) || filepath.Base(p.ID) != p.ID || (p.Architecture != "amd64" && p.Architecture != "arm64") || len(p.Artifacts) == 0 || p.SizeBytes <= 0 || p.InstalledBytes <= 0 {
		return errors.New("invalid native package")
	}
	seen := map[string]bool{}
	var size int64
	for _, a := range p.Artifacts {
		u, err := url.Parse(a.URL)
		hash, hashErr := hex.DecodeString(a.SHA256)
		if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.Fragment != "" || hashErr != nil || len(hash) != 32 || a.Size <= 0 || a.Size > 512<<20 || !filepath.IsLocal(a.Name) || filepath.Base(a.Name) != a.Name || seen[a.Name] || (a.Format != "apk" && a.Format != "html5") {
			return fmt.Errorf("invalid native artifact %q", a.Name)
		}
		seen[a.Name] = true
		size += a.Size
	}
	if size != p.SizeBytes || size > 2<<30 || p.InstalledBytes > 4<<30 {
		return errors.New("invalid native package size")
	}
	return nil
}

func (p Package) Digest() string {
	data, _ := json.Marshal(p)
	hash := sha256.Sum256(data)
	return hex.EncodeToString(hash[:])
}
