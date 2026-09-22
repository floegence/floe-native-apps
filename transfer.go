package nativeapps

import (
	"context"
	"errors"
	"io"
	"os"

	"github.com/floegence/floe-native-apps/artifactcache"
)

// TransferPlan contains only identities from the receiver's trusted catalog.
// It is a cache observation, not an authorization or a reservation of cache files.
type TransferPlan struct {
	PackageDigest    string   `json:"package_digest"`
	Architecture     string   `json:"architecture"`
	MissingArtifacts []string `json:"missing_artifacts"`
	MissingBytes     int64    `json:"missing_bytes"`
}

// Plan verifies archives only on explicit preparation/inspection, never on the
// normal application-open or status path. It performs no network requests.
func (m *Manager) Plan(ctx context.Context) (TransferPlan, error) {
	m.mu.Lock()
	closed := m.closed
	m.mu.Unlock()
	if closed {
		return TransferPlan{}, ErrInvalid
	}
	plan := TransferPlan{PackageDigest: m.installations[0].Digest, Architecture: m.pkg.Architecture, MissingArtifacts: []string{}}
	for _, a := range m.pkg.Artifacts {
		if err := ctx.Err(); err != nil {
			return TransferPlan{}, err
		}
		if err := artifactcache.Verify(ctx, m.artifactPath(a), archiveSpec(a)); err != nil {
			if !errors.Is(err, os.ErrNotExist) && !errors.Is(err, artifactcache.ErrIntegrity) {
				return TransferPlan{}, err
			}
			plan.MissingArtifacts = append(plan.MissingArtifacts, a.SHA256)
			plan.MissingBytes += a.Size
		}
	}
	return plan, nil
}

// WriteTransferBundle accepts only a subset of the supplied trusted recipe.
// Consumer adapters must resolve pkg from their compiled catalog, not a client.
// An empty plan needs no bundle: use Manager.Start with source "cache" instead.
func WriteTransferBundle(ctx context.Context, pkg Package, plan TransferPlan, cacheRoot string, output io.Writer, progress func(int64)) error {
	if err := pkg.Validate(); err != nil {
		return err
	}
	if plan.PackageDigest != pkg.Digest() || plan.Architecture != pkg.Architecture {
		return ErrInvalid
	}
	selected := map[string]bool{}
	for _, digest := range plan.MissingArtifacts {
		if selected[digest] {
			return ErrInvalid
		}
		selected[digest] = true
	}
	var artifacts []Artifact
	var size int64
	for _, artifact := range pkg.Artifacts {
		if selected[artifact.SHA256] {
			artifacts = append(artifacts, artifact)
			size += artifact.Size
			delete(selected, artifact.SHA256)
		}
	}
	if len(selected) != 0 || size != plan.MissingBytes {
		return ErrInvalid
	}
	return writeBundle(ctx, artifacts, cacheRoot, output, progress)
}
