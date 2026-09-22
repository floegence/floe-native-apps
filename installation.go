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

// Published recipes remain supported until an explicit compatibility change.
// Do not change Package's digest encoding when extending this metadata.
func compatibleInstallations(pkg Package) []Installation {
	current := Installation{ID: pkg.ID, Digest: pkg.Digest(), Architecture: pkg.Architecture, Contract: "xpra-6-private-v1"}
	result := []Installation{current}
	pinned, err := ForPlatform("linux", pkg.Architecture)
	if err != nil || pinned.Digest() != pkg.Digest() {
		return result
	}
	legacy := map[string]string{
		"amd64": "0a30ff8f725be97588b1f69714fb9487b3235c62c21fafe338cbbeae16bbb9e6",
		"arm64": "98010db370ba2ebda4c5c6c9c90d100fc29acca319f293de458680235a47595b",
	}
	return append(result, Installation{ID: "alpine-3.23-xpra-6.2.2-" + pkg.Architecture + "-r1", Digest: legacy[pkg.Architecture], Architecture: pkg.Architecture, Contract: current.Contract})
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
	if _, ok := m.installation(digest); !ok {
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
	if _, err := ResolveTools(root); err != nil {
		return "", err
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
