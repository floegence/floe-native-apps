package nativeapps

import (
	"errors"
	"os"
	"path/filepath"
)

// Installation identifies an installed recipe independently of the SDK release.
// Ready is a lightweight check of its private marker and required tool layout.
type Installation struct {
	ID           string `json:"id"`
	Digest       string `json:"digest"`
	Architecture string `json:"architecture"`
	Contract     string `json:"contract"`
	Ready        bool   `json:"ready"`
}

// The r2 native artifacts are unchanged by the r3 image-decoder closure.
var desktopR2Digests = map[string]string{
	"amd64": "f8f29cf383166c4e7c6728b6c1f696ab015391875dc2a370f283d4d4ca457a90",
	"arm64": "93a0210e630a54d176f9b8f6ff3fc21605a348d6f97c7c5c8127fa6cd7a484be",
}

// Published recipes remain supported until an explicit compatibility change.
// Do not change Package's digest encoding when extending this metadata.
func compatibleInstallations(pkg Package) []Installation {
	current := Installation{ID: pkg.ID, Digest: pkg.Digest(), Architecture: pkg.Architecture, Contract: "xpra-6-private-v1"}
	if pkg.Preparation != nil {
		current.Contract = pkg.Preparation.Contract
		result := []Installation{current}
		if current.Contract == hostDesktopContract {
			releases, err := hostDesktopReleases()
			if err == nil {
				for _, release := range releases {
					if release.Architecture == pkg.Architecture && release.Digest != current.Digest {
						result = append(result, Installation{ID: release.ID, Digest: release.Digest,
							Architecture: release.Architecture, Contract: hostDesktopContract})
					}
				}
			}
			return result
		}
		previous := map[string]string{
			"amd64": "ee42fb12933a2ef4d1d1efbd1cddd9e5860817155726d041395fa726810a4023",
			"arm64": "9ec255e3234f2b587bbc4bf3eb9882791f3d847f8c0f1648080f6cfc52a256fb",
		}
		pinned, err := DesktopForPlatform("linux", pkg.Architecture)
		if err != nil || pinned.Digest() != pkg.Digest() {
			return result
		}
		result = append(result, Installation{ID: "alpine-3.23-desktop-14.0.2-" + pkg.Architecture + "-r2",
			Digest: desktopR2Digests[pkg.Architecture], Architecture: pkg.Architecture, Contract: current.Contract})
		result = append(result, Installation{ID: "alpine-3.23-desktop-14.0.2-" + pkg.Architecture + "-r1",
			Digest: previous[pkg.Architecture], Architecture: pkg.Architecture, Contract: current.Contract})
		xpra, err := ForPlatform("linux", pkg.Architecture)
		if err != nil {
			return result
		}
		return append(result, compatibleInstallations(xpra)...)
	}
	result := []Installation{current}
	pinned, err := ForPlatform("linux", pkg.Architecture)
	if err != nil || pinned.Digest() != pkg.Digest() {
		return result
	}
	legacy := map[string]string{
		"amd64": "0a30ff8f725be97588b1f69714fb9487b3235c62c21fafe338cbbeae16bbb9e6",
		"arm64": "98010db370ba2ebda4c5c6c9c90d100fc29acca319f293de458680235a47595b",
	}
	previous := map[string]string{
		"amd64": "1005a76b31941a66417759f4932ac5355b179bcff4125c0d19b6ff1fe0e8352b",
		"arm64": "4379638ec87588ad81e96cd3fc8d91a0cd3189b779f9962cece9e568a48276fa",
	}
	return append(result,
		Installation{ID: "alpine-3.23-xpra-6.2.2-" + pkg.Architecture + "-r2", Digest: previous[pkg.Architecture], Architecture: pkg.Architecture, Contract: current.Contract},
		Installation{ID: "alpine-3.23-xpra-6.2.2-" + pkg.Architecture + "-r1", Digest: legacy[pkg.Architecture], Architecture: pkg.Architecture, Contract: current.Contract})
}

func (m *Manager) installation(digest string) (Installation, bool) {
	for _, item := range m.installations {
		if item.Digest == digest {
			return item, true
		}
	}
	return Installation{}, false
}

func (m *Manager) installedDirectory(digest string) (string, error) {
	item, ok := m.installation(digest)
	if !ok {
		return "", ErrInvalid
	}
	root := filepath.Join(m.root, "packages", digest)
	info, err := os.Lstat(root)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return "", ErrInvalid
	}
	data, err := os.ReadFile(filepath.Join(root, ".native-apps"))
	if err != nil || string(data) != digest {
		return "", ErrInvalid
	}
	switch item.Contract {
	case "xpra-6-private-v1":
		if _, err := ResolveTools(root); err != nil {
			return "", err
		}
	case desktopContract:
		if _, err := ResolveDesktopTools(root, item.Architecture); err != nil {
			return "", err
		}
	case hostDesktopContract:
		if _, err := resolveHostDesktopInstallation(root, item); err != nil {
			return "", err
		}
	default:
		return "", ErrUnsupported
	}
	return root, nil
}

// DirectoryFor resolves an exact supported installation for a surviving process.
// It never changes the current installation or prepares another version.
func (m *Manager) DirectoryFor(digest string) (string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed {
		return "", ErrInvalid
	}
	return m.installedDirectory(digest)
}

func (m *Manager) installedSnapshot() (*Installation, string) {
	if m.op.Installed == "" {
		return nil, m.installationError
	}
	item, ok := m.installation(m.op.Installed)
	if !ok {
		return nil, "unsupported_installation"
	}
	_, err := m.installedDirectory(item.Digest)
	item.Ready = err == nil
	if err != nil {
		return &item, "installation_damaged"
	}
	return &item, ""
}

// adoptInstallation upgrades the v1 operation record without rewriting package
// identities or touching installed files. Explicit v2 selection is authoritative.
func (m *Manager) adoptInstallation(savedVersion int) error {
	if m.op.Package != "" {
		if _, ok := m.installation(m.op.Package); !ok {
			return errors.New("unsupported native preparation package")
		}
	}
	if savedVersion == 2 {
		if m.op.Installed != "" {
			if _, ok := m.installation(m.op.Installed); !ok {
				return errors.New("unsupported installed native component")
			}
		}
		return nil
	}
	if m.op.Status.State == "ready" {
		m.op.Installed = m.op.Package
		return nil
	}
	for _, item := range m.installations {
		if _, err := m.installedDirectory(item.Digest); err == nil {
			m.op.Installed = item.Digest
			return nil
		}
	}
	entries, err := os.ReadDir(filepath.Join(m.root, "packages"))
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	for _, entry := range entries {
		if _, known := m.installation(entry.Name()); known {
			m.op.Installed = entry.Name()
			m.installationError = "installation_damaged"
			return nil
		}
		m.installationError = "unsupported_installation"
	}
	return nil
}
