package nativeapps

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"syscall"
	"testing"
)

func TestPublishedRecipeIdentitiesRemainStable(t *testing.T) {
	for arch, digest := range map[string]string{"amd64": "1005a76b31941a66417759f4932ac5355b179bcff4125c0d19b6ff1fe0e8352b", "arm64": "4379638ec87588ad81e96cd3fc8d91a0cd3189b779f9962cece9e568a48276fa"} {
		pkg, err := ForPlatform("linux", arch)
		if err != nil {
			t.Fatal(err)
		}
		known := compatibleInstallations(pkg)
		if len(known) != 3 || known[0].Digest != pkg.Digest() || known[1].Digest != digest || known[0].Digest == digest {
			t.Fatal(known)
		}
		legacy := map[string]string{"amd64": "0a30ff8f725be97588b1f69714fb9487b3235c62c21fafe338cbbeae16bbb9e6", "arm64": "98010db370ba2ebda4c5c6c9c90d100fc29acca319f293de458680235a47595b"}
		if known[2].Digest != legacy[arch] {
			t.Fatal("published r1 identity changed", known[2])
		}
		for index, recipe := range known {
			if recipe.Architecture != arch || recipe.Contract != known[0].Contract || recipe.ID != fmt.Sprintf("alpine-3.23-xpra-6.2.2-%s-r%d", arch, 3-index) {
				t.Fatal("published recipe contract changed", recipe)
			}
		}
	}
}

func TestUnknownInstallationRecordsArePreserved(t *testing.T) {
	for _, change := range []func(*operation){
		func(op *operation) { op.Version = 3 },
		func(op *operation) { op.Version = 2; op.Installed = "unreviewed" },
		func(op *operation) { op.Package = "unreviewed" },
	} {
		root, pkg := legacyInstallation(t)
		data, _ := os.ReadFile(filepath.Join(root, "operation.json"))
		var saved operation
		if err := json.Unmarshal(data, &saved); err != nil {
			t.Fatal(err)
		}
		change(&saved)
		data, _ = json.Marshal(saved)
		if err := os.WriteFile(filepath.Join(root, "operation.json"), data, 0600); err != nil {
			t.Fatal(err)
		}
		if m, err := New(root, pkg, nil); err == nil {
			m.Close()
			t.Fatal("accepted unknown record")
		}
		after, _ := os.ReadFile(filepath.Join(root, "operation.json"))
		if !bytes.Equal(data, after) {
			t.Fatal("rewrote rejected state")
		}
	}
}

func TestIncrementalRelayUploadsOnlyMissingArchive(t *testing.T) {
	m, data := testManager(t, func(context.Context, string) error { return nil })
	other := fixtureArchive(t, &tar.Header{Name: "second", Typeflag: tar.TypeReg, Size: 5})
	artifact := m.pkg.Artifacts[0]
	artifact.Name = "second.apk"
	artifact.SHA256 = fmt.Sprintf("%x", sha256.Sum256(other))
	artifact.Size = int64(len(other))
	m.pkg.Artifacts = append(m.pkg.Artifacts, artifact)
	m.pkg.SizeBytes += artifact.Size
	m.installations = compatibleInstallations(m.pkg)
	if err := os.MkdirAll(filepath.Join(m.root, "archives"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(m.artifactPath(m.pkg.Artifacts[0]), data, 0600); err != nil {
		t.Fatal(err)
	}
	plan, err := m.Plan(context.Background())
	if err != nil || len(plan.MissingArtifacts) != 1 || plan.MissingBytes != artifact.Size {
		t.Fatal(plan, err)
	}
	cache := t.TempDir()
	if err := os.MkdirAll(filepath.Join(cache, "archives"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(cache, "archives", artifact.SHA256), other, 0600); err != nil {
		t.Fatal(err)
	}
	var bundle bytes.Buffer
	if err := WriteTransferBundle(context.Background(), m.pkg, plan, cache, &bundle, nil); err != nil {
		t.Fatal(err)
	}
	z, err := zip.NewReader(bytes.NewReader(bundle.Bytes()), int64(bundle.Len()))
	if err != nil || len(z.File) != 1 || z.File[0].Name != artifact.Name {
		t.Fatal("incorrect relay inventory", err)
	}
	upload(t, m, bundle.Bytes())
	waitState(t, m, "ready")
	root, err := m.Directory()
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"payload", "second"} {
		if _, err := os.Stat(filepath.Join(root, name)); err != nil {
			t.Fatal(err)
		}
	}
}

func TestActivationPersistenceFailureRetainsPreviousInstallation(t *testing.T) {
	m, data := testManager(t, nil)
	m.installations = append(m.installations, Installation{ID: "previous-fixture", Digest: legacyAMD64, Architecture: "amd64", Contract: "fixture"})
	previous := filepath.Join(m.root, "packages", legacyAMD64)
	if err := m.prepare(context.Background(), previous, "amd64"); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(previous, ".native-apps"), []byte(legacyAMD64), 0600); err != nil {
		t.Fatal(err)
	}
	m.op.Installed = legacyAMD64
	m.validate = func(context.Context, string) error {
		if err := os.Rename(filepath.Join(m.root, "operation.json"), filepath.Join(m.root, "retained-operation.json")); err != nil {
			return err
		}
		return os.Mkdir(filepath.Join(m.root, "operation.json"), 0700)
	}
	upload(t, m, zipFixture(t, "native.apk", data))
	waitState(t, m, "failed")
	if dir, err := m.Directory(); err != nil || dir != previous {
		t.Fatal("changed selection after failed persistence", dir, err)
	}
}

// This is the published amd64 r1 identity, not a renamed current installation.
const legacyAMD64 = "0a30ff8f725be97588b1f69714fb9487b3235c62c21fafe338cbbeae16bbb9e6"

func legacyInstallation(t *testing.T) (string, Package) {
	t.Helper()
	m, _ := testManager(t, nil)
	root := m.root
	directory := filepath.Join(root, "packages", legacyAMD64)
	if err := m.prepare(context.Background(), directory, "amd64"); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, ".native-apps"), []byte(legacyAMD64), 0600); err != nil {
		t.Fatal(err)
	}
	m.Close()
	saved := operation{Version: 1, Package: legacyAMD64, Status: Status{State: "ready"}}
	data, _ := json.Marshal(saved)
	if err := os.WriteFile(filepath.Join(root, "operation.json"), data, 0600); err != nil {
		t.Fatal(err)
	}
	pkg, err := ForPlatform("linux", "amd64")
	if err != nil {
		t.Fatal(err)
	}
	return root, pkg
}

func TestRuntimeUpdatePreservesPublishedLegacyInstallation(t *testing.T) {
	root, pkg := legacyInstallation(t)
	m, err := New(root, pkg, func(context.Context, string) error {
		t.Fatal("status must not qualify installed components")
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	directory, err := m.Directory()
	if err != nil || directory != filepath.Join(root, "packages", legacyAMD64) {
		t.Fatalf("runtime update disabled the installed component: %q %v", directory, err)
	}
	status := m.Snapshot("owner")
	if !status.UpdateAvailable || status.Installed == nil || !status.Installed.Ready || status.Installed.Digest != legacyAMD64 {
		t.Fatal(status)
	}
	data, _ := os.ReadFile(filepath.Join(root, "operation.json"))
	var saved operation
	if json.Unmarshal(data, &saved) != nil || saved.Version != 2 || saved.Installed != legacyAMD64 {
		t.Fatal(string(data))
	}
	m.Close()
	m, err = New(root, pkg, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	if !m.Snapshot("owner").Installed.Ready {
		t.Fatal("lost migrated selection")
	}
}

func TestLegacyUpdateFailureAndCancellationKeepInstallation(t *testing.T) {
	for _, action := range []string{"invalid", "cancel", "interrupt"} {
		t.Run(action, func(t *testing.T) {
			root, pkg := legacyInstallation(t)
			m, err := New(root, pkg, nil)
			if err != nil {
				t.Fatal(err)
			}
			defer m.Close()
			s, err := m.Start("owner", "update", "upload", 10)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := m.Directory(); err != nil {
				t.Fatal("update disabled the current component", err)
			}
			switch action {
			case "cancel":
				_, err = m.Cancel("owner", s.OperationID)
			case "interrupt":
				m.Close()
			case "invalid":
				_, err = m.WriteChunk("owner", s.OperationID, 0, []byte("not a zip!"))
				if err != nil {
					t.Fatal(err)
				}
				_, err = m.CompleteUpload("owner", s.OperationID)
				if err != nil {
					t.Fatal(err)
				}
				waitState(t, m, "failed")
			}
			if err != nil {
				t.Fatal(err)
			}
			m.Close()
			m, err = New(root, pkg, nil)
			if err != nil {
				t.Fatal(err)
			}
			defer m.Close()
			if s := m.Snapshot("owner"); s.Installed == nil || !s.Installed.Ready || !s.UpdateAvailable {
				t.Fatal(s)
			}
		})
	}
}

type rejectNetwork struct{ t *testing.T }

func (r rejectNetwork) RoundTrip(*http.Request) (*http.Response, error) {
	r.t.Error("unexpected network request")
	return nil, errors.New("network forbidden")
}

func TestCacheOnlyPreparationAndPlan(t *testing.T) {
	m, data := testManager(t, func(context.Context, string) error { return nil })
	m.client = &http.Client{Transport: rejectNetwork{t}}
	plan, err := m.Plan(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if plan.MissingBytes != int64(len(data)) || len(plan.MissingArtifacts) != 1 {
		t.Fatal(plan)
	}
	if _, err := m.Start("owner", "missing", "cache", 0); err != nil {
		t.Fatal(err)
	}
	waitState(t, m, "failed")
	if m.Snapshot("owner").ErrorCode != "cache_changed" {
		t.Fatal(m.Snapshot("owner"))
	}
	if err := os.MkdirAll(filepath.Join(m.root, "archives"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(m.artifactPath(m.pkg.Artifacts[0]), data, 0600); err != nil {
		t.Fatal(err)
	}
	plan, err = m.Plan(context.Background())
	if err != nil || plan.MissingBytes != 0 || len(plan.MissingArtifacts) != 0 {
		t.Fatal(plan, err)
	}
	if _, err := m.Start("owner", "cached", "cache", 0); err != nil {
		t.Fatal(err)
	}
	waitState(t, m, "ready")
	s := m.Snapshot("owner")
	if s.ExpectedBytes != 0 || s.ReceivedBytes != 0 || s.UpdateAvailable || s.Installed == nil || !s.Installed.Ready {
		t.Fatal(s)
	}
}

func TestFailedQualificationKeepsPreviousSelection(t *testing.T) {
	for _, failure := range []error{errors.New("qualification failed"), syscall.ENOSPC} {
		t.Run(failure.Error(), func(t *testing.T) {
			m, data := testManager(t, func(context.Context, string) error { return failure })
			legacy := Installation{ID: "previous-fixture", Digest: legacyAMD64, Architecture: "amd64", Contract: "fixture"}
			m.installations = append(m.installations, legacy)
			root := filepath.Join(m.root, "packages", legacy.Digest)
			if err := m.prepare(context.Background(), root, "amd64"); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(root, ".native-apps"), []byte(legacy.Digest), 0600); err != nil {
				t.Fatal(err)
			}
			m.op.Installed = legacy.Digest
			upload(t, m, zipFixture(t, "native.apk", data))
			waitState(t, m, "failed")
			if got, err := m.Directory(); err != nil || got != root {
				t.Fatal("lost usable installation", got, err)
			}
			if m.installed() {
				t.Fatal("activated an unqualified package")
			}
		})
	}
}

func TestPartialBundleRequiresCompleteVerifiedUnion(t *testing.T) {
	for _, cached := range []bool{false, true} {
		t.Run(map[bool]string{false: "missing", true: "cached"}[cached], func(t *testing.T) {
			m, data := testManager(t, func(context.Context, string) error { return nil })
			if cached {
				if err := os.MkdirAll(filepath.Join(m.root, "archives"), 0700); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(m.artifactPath(m.pkg.Artifacts[0]), data, 0600); err != nil {
					t.Fatal(err)
				}
			}
			var empty bytes.Buffer
			if err := zip.NewWriter(&empty).Close(); err != nil {
				t.Fatal(err)
			}
			upload(t, m, empty.Bytes())
			if cached {
				waitState(t, m, "ready")
			} else {
				waitState(t, m, "failed")
			}
		})
	}
}

func TestTransferBundleRejectsUntrustedIdentities(t *testing.T) {
	pkg, _ := testPackage(t)
	for _, plan := range []TransferPlan{
		{PackageDigest: "other", Architecture: pkg.Architecture},
		{PackageDigest: pkg.Digest(), Architecture: "arm64"},
		{PackageDigest: pkg.Digest(), Architecture: pkg.Architecture, MissingArtifacts: []string{"untrusted"}},
		{PackageDigest: pkg.Digest(), Architecture: pkg.Architecture, MissingArtifacts: []string{pkg.Artifacts[0].SHA256, pkg.Artifacts[0].SHA256}},
	} {
		if err := WriteTransferBundle(context.Background(), pkg, plan, t.TempDir(), &bytes.Buffer{}, nil); !errors.Is(err, ErrInvalid) {
			t.Fatal("accepted untrusted plan", err)
		}
	}
}

func TestDamagedInstallationDoesNotBecomeUninstalled(t *testing.T) {
	root, pkg := legacyInstallation(t)
	if err := os.Remove(filepath.Join(root, "packages", legacyAMD64, "floe/bin/xpra")); err != nil {
		t.Fatal(err)
	}
	m, err := New(root, pkg, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	s := m.Snapshot("owner")
	if s.Installed == nil || s.Installed.Ready || s.InstallationErrorCode != "installation_damaged" {
		t.Fatal(s)
	}
}
