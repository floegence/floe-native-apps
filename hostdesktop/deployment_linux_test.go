//go:build linux

package hostdesktop

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
)

type fakeLoginSystemd struct {
	active, enabled bool
	fail            string
	cancel          context.CancelFunc
	calls           []string
}

func (s *fakeLoginSystemd) run(ctx context.Context, args ...string) error {
	if ctx.Err() != nil {
		return ctx.Err()
	}
	operation := args[0]
	s.calls = append(s.calls, strings.Join(args, " "))
	if operation == s.fail {
		s.fail = ""
		return errors.New("fixture systemd failure")
	}
	switch operation {
	case "is-active":
		if !s.active {
			return errors.New("inactive")
		}
	case "is-enabled":
		if !s.enabled {
			return errors.New("disabled")
		}
	case "start":
		s.active = true
	case "stop":
		s.active = false
	case "enable":
		s.enabled = true
	case "disable":
		s.enabled = false
	}
	if operation == "start" && s.cancel != nil {
		s.cancel()
		s.cancel = nil
	}
	return nil
}
func fixtureLoginDeployment(t *testing.T) (*loginDeployer, LoginServiceDeploymentRequest, *fakeLoginSystemd, *[]LoginServiceDeploymentEvent) {
	t.Helper()
	base := t.TempDir()
	source := filepath.Join(base, "source")
	if err := os.Mkdir(source, 0700); err != nil {
		t.Fatal(err)
	}
	digest := func(name string) string {
		data := []byte(name)
		if err := os.WriteFile(filepath.Join(source, name), data, 0755); err != nil {
			t.Fatal(err)
		}
		hash := sha256.Sum256(data)
		return hex.EncodeToString(hash[:])
	}
	request := LoginServiceDeploymentRequest{Operation: "ensure", SourceDirectory: source, RuntimeUID: 1000, RuntimeGID: 1000, RuntimeSHA256: strings.Repeat("a", 64), ServiceSHA256: digest("floe-host-desktop-service"), WorkerSHA256: digest("desktop-drm")}
	var media bytes.Buffer
	compressed := gzip.NewWriter(&media)
	archive := tar.NewWriter(compressed)
	for _, name := range []string{"floe/host-desktop/python3", "floe/host-desktop/host_desktop_drm.py", "floe/host-desktop/host_desktop_login_text.py", "usr/bin/python3", "usr/share/empty-marker"} {
		data := []byte("media fixture")
		if name == "usr/share/empty-marker" {
			data = nil
		}
		if archive.WriteHeader(&tar.Header{Name: name, Mode: 0755, Size: int64(len(data)), Typeflag: tar.TypeReg}) != nil {
			t.Fatal("media fixture header")
		}
		if _, err := archive.Write(data); err != nil {
			t.Fatal(err)
		}
	}
	if archive.Close() != nil || compressed.Close() != nil {
		t.Fatal("media fixture close")
	}
	if err := os.WriteFile(filepath.Join(source, "media.tar.gz"), media.Bytes(), 0600); err != nil {
		t.Fatal(err)
	}
	mediaDigest := sha256.Sum256(media.Bytes())
	request.MediaSHA256 = hex.EncodeToString(mediaDigest[:])
	licensePath := os.Getenv("FLOE_LOGIN_TEST_LICENSE")
	if licensePath == "" {
		licensePath = "../native/host-desktop/drm/dist/amd64/libdrmtap.LICENSE"
	}
	license, err := os.ReadFile(licensePath)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(source, "libdrmtap.LICENSE"), license, 0644); err != nil {
		t.Fatal(err)
	}
	systemd := &fakeLoginSystemd{}
	events := []LoginServiceDeploymentEvent{}
	d := &loginDeployer{root: filepath.Join(base, "installed"), unit: filepath.Join(base, "redeven-desktop.service"), systemctl: systemd.run, report: func(event LoginServiceDeploymentEvent) { events = append(events, event) }}
	return d, request, systemd, &events
}
func TestLoginDeploymentInstallAndIdempotentUpdate(t *testing.T) {
	d, request, systemd, _ := fixtureLoginDeployment(t)
	if err := d.manage(context.Background(), request); err != nil {
		t.Fatal(err)
	}
	record, err := d.readRecord()
	if err != nil || record == nil {
		t.Fatal("installation policy not committed")
	}
	if bytes.Contains(record.raw, []byte("source_directory")) || bytes.Contains(record.raw, []byte("operation")) {
		t.Fatal("transfer paths persisted in policy")
	}
	if !systemd.active || !systemd.enabled {
		t.Fatal("service not automatically started")
	}
	unit, _ := os.ReadFile(d.unit)
	if !bytes.Contains(unit, []byte("StandardInput=null")) || bytes.Contains(unit, []byte(request.SourceDirectory)) {
		t.Fatal("service unit inherited transfer or credential input")
	}
	for _, setting := range []string{"ProtectHome=tmpfs", "BindReadOnlyPaths=/run/user", "RestrictAddressFamilies=AF_UNIX", "NoNewPrivileges=yes"} {
		if !bytes.Contains(unit, []byte(setting)) {
			t.Fatalf("unit lost %s", setting)
		}
	}
	if bytes.Contains(unit, []byte("CAP_SYS_MODULE")) {
		t.Fatal("steady-state service can load kernel modules")
	}
	request.Operation = "ensure"
	if err = d.manage(context.Background(), request); err != nil {
		t.Fatal(err)
	}
	if err = d.manage(context.Background(), LoginServiceDeploymentRequest{Operation: "stop"}); err != nil {
		t.Fatal(err)
	}
	if systemd.active {
		t.Fatal("stop retained service")
	}
	if err = d.manage(context.Background(), func() LoginServiceDeploymentRequest { r := request; r.Operation = "ensure"; return r }()); err != nil {
		t.Fatal(err)
	}
	if err = d.manage(context.Background(), LoginServiceDeploymentRequest{Operation: "uninstall"}); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{d.root, d.unit, d.root + ".removing"} {
		if _, err := os.Lstat(path); !errors.Is(err, os.ErrNotExist) {
			t.Fatalf("uninstall retained %s", filepath.Base(path))
		}
	}
}
func TestLoginDeploymentRestoreRetainedVersion(t *testing.T) {
	d, original, systemd, _ := fixtureLoginDeployment(t)
	if err := d.manage(context.Background(), original); err != nil {
		t.Fatal(err)
	}
	first, _ := d.readRecord()
	next := original
	next.Operation, next.RuntimeSHA256 = "ensure", strings.Repeat("b", 64)
	if err := d.manage(context.Background(), next); err != nil {
		t.Fatal(err)
	}
	original.Operation = "ensure"
	if err := d.manage(context.Background(), original); err != nil {
		t.Fatalf("restore retained version: %v", err)
	}
	restored, _ := d.readRecord()
	if restored.Digest != first.Digest || restored.Request != first.Request || !systemd.active {
		t.Fatal("restore did not retain the original policy and active service")
	}
}

func TestLoginDeploymentRejectsAlteredRetainedVersion(t *testing.T) {
	for _, alteration := range []string{"content", "missing", "extra", "symlink", "permissions", "owner"} {
		t.Run(alteration, func(t *testing.T) {
			if alteration == "owner" && os.Geteuid() != 0 {
				t.Skip("owner mutation requires the explicit root qualification run")
			}
			d, original, systemd, _ := fixtureLoginDeployment(t)
			if err := d.manage(context.Background(), original); err != nil {
				t.Fatal(err)
			}
			first, _ := d.readRecord()
			next := original
			next.Operation, next.RuntimeSHA256 = "ensure", strings.Repeat("b", 64)
			if err := d.manage(context.Background(), next); err != nil {
				t.Fatal(err)
			}
			before, _ := os.ReadFile(filepath.Join(d.root, "installed.json"))
			path := filepath.Join(d.root, "versions", first.Digest, "media", "floe", "host-desktop", "host_desktop_drm.py")
			var err error
			switch alteration {
			case "content":
				err = os.WriteFile(path, []byte("changed bytes"), 0755)
			case "missing":
				err = os.Remove(path)
			case "extra":
				err = os.WriteFile(path+".extra", []byte("unverified"), 0644)
			case "symlink":
				if err = os.Remove(path); err == nil {
					err = os.Symlink(filepath.Join(original.SourceDirectory, "desktop-drm"), path)
				}
			case "permissions":
				err = os.Chmod(path, 0777)
			case "owner":
				err = os.Chown(path, 1000, -1)
			}
			if err != nil {
				t.Fatal(err)
			}
			original.Operation = "ensure"
			if err = d.manage(context.Background(), original); err == nil {
				t.Fatal("altered retained version was activated")
			}
			after, _ := os.ReadFile(filepath.Join(d.root, "installed.json"))
			if !bytes.Equal(before, after) || !systemd.active {
				t.Fatal("rejected restore changed the usable service")
			}
		})
	}
}

func TestLoginDeploymentFailedRestorePreservesBothVersions(t *testing.T) {
	d, original, systemd, _ := fixtureLoginDeployment(t)
	if err := d.manage(context.Background(), original); err != nil {
		t.Fatal(err)
	}
	first, _ := d.readRecord()
	next := original
	next.Operation, next.RuntimeSHA256 = "ensure", strings.Repeat("b", 64)
	if err := d.manage(context.Background(), next); err != nil {
		t.Fatal(err)
	}
	second, _ := d.readRecord()
	systemd.fail = "start"
	original.Operation = "ensure"
	if err := d.manage(context.Background(), original); err == nil {
		t.Fatal("failed restore reported success")
	}
	current, _ := d.readRecord()
	if current.Digest != second.Digest || !systemd.active {
		t.Fatal("failed restore did not restore the current service")
	}
	for _, digest := range []string{first.Digest, second.Digest} {
		if _, err := os.Stat(filepath.Join(d.root, "versions", digest)); err != nil {
			t.Fatal("failed restore removed a retained version")
		}
	}
}

func TestLoginDeploymentPrivateSSHUmaskPreservesConverterTraversal(t *testing.T) {
	d, request, systemd, _ := fixtureLoginDeployment(t)
	previous := syscall.Umask(0077)
	defer syscall.Umask(previous)
	if err := d.manage(context.Background(), request); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{d.root, filepath.Join(d.root, "versions")} {
		info, err := os.Stat(path)
		if err != nil || info.Mode().Perm() != 0755 {
			t.Fatal("SSH umask hid the converter executable")
		}
	}
	policy, err := os.Stat(filepath.Join(d.root, "installed.json"))
	if err != nil || policy.Mode().Perm() != 0600 {
		t.Fatal("policy became readable by the converter")
	}
	for _, path := range []string{d.root, filepath.Join(d.root, "versions")} {
		if os.Chmod(path, 0700) != nil {
			t.Fatal("fixture permission change failed")
		}
	}
	request.Operation, request.RuntimeSHA256 = "ensure", strings.Repeat("b", 64)
	systemd.fail = "start"
	if d.manage(context.Background(), request) == nil {
		t.Fatal("failed update was accepted")
	}
	for _, path := range []string{d.root, filepath.Join(d.root, "versions")} {
		info, err := os.Stat(path)
		if err != nil || info.Mode().Perm() != 0700 {
			t.Fatal("rollback changed the previous directory permissions")
		}
	}
}

func TestLoginDeploymentFailuresLeaveNoInstallation(t *testing.T) {
	for _, failure := range []string{"daemon-reload", "enable", "start", "is-active"} {
		t.Run(failure, func(t *testing.T) {
			d, request, systemd, events := fixtureLoginDeployment(t)
			systemd.fail = failure
			if d.manage(context.Background(), request) == nil {
				t.Fatal("failure accepted")
			}
			for _, path := range []string{d.root, d.unit} {
				if _, err := os.Lstat(path); !errors.Is(err, os.ErrNotExist) {
					t.Fatal("partial installation retained")
				}
			}
			if systemd.active || systemd.enabled {
				t.Fatal("failed service retained activation")
			}
			last := (*events)[len(*events)-1]
			if last.Stage != "rolled_back" || last.Rollback != "complete" {
				t.Fatal("rollback outcome unavailable")
			}
		})
	}
}
func TestLoginDeploymentCanceledStartRollsBack(t *testing.T) {
	d, request, systemd, _ := fixtureLoginDeployment(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	systemd.cancel = cancel
	if d.manage(ctx, request) == nil {
		t.Fatal("cancellation accepted")
	}
	if systemd.active || systemd.enabled {
		t.Fatal("canceled installation retained activation")
	}
	if _, err := os.Stat(d.root); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("canceled installation retained files")
	}
}
func TestLoginDeploymentUpdateFailurePreservesExactOldPolicy(t *testing.T) {
	d, request, systemd, _ := fixtureLoginDeployment(t)
	if err := d.manage(context.Background(), request); err != nil {
		t.Fatal(err)
	}
	oldUnit, _ := os.ReadFile(d.unit)
	oldPolicy, _ := os.ReadFile(filepath.Join(d.root, "installed.json"))
	oldRecord, _ := d.readRecord()
	request.Operation = "ensure"
	request.RuntimeSHA256 = strings.Repeat("b", 64)
	systemd.fail = "start"
	if d.manage(context.Background(), request) == nil {
		t.Fatal("update failure accepted")
	}
	unit, _ := os.ReadFile(d.unit)
	policy, _ := os.ReadFile(filepath.Join(d.root, "installed.json"))
	if !bytes.Equal(oldUnit, unit) || !bytes.Equal(oldPolicy, policy) || !systemd.active || !systemd.enabled {
		t.Fatal("old deployment not restored exactly")
	}
	directories, _ := os.ReadDir(filepath.Join(d.root, "versions"))
	if len(directories) != 1 || directories[0].Name() != oldRecord.Digest {
		t.Fatal("failed update retained a version")
	}
}
func TestLoginDeploymentRejectsDigestSymlinkAndUnrelatedUnit(t *testing.T) {
	for _, mode := range []string{"digest", "symlink", "unit"} {
		t.Run(mode, func(t *testing.T) {
			d, request, systemd, _ := fixtureLoginDeployment(t)
			switch mode {
			case "digest":
				request.WorkerSHA256 = strings.Repeat("c", 64)
			case "symlink":
				path := filepath.Join(request.SourceDirectory, "desktop-drm")
				if err := os.Rename(path, path+".source"); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(path+".source", path); err != nil {
					t.Fatal(err)
				}
			case "unit":
				if err := os.WriteFile(d.unit, []byte("unrelated unit"), 0644); err != nil {
					t.Fatal(err)
				}
			}
			if d.manage(context.Background(), request) == nil {
				t.Fatal("untrusted installation admitted")
			}
			if systemd.active || systemd.enabled {
				t.Fatal("rejected installation activated")
			}
			if mode == "unit" {
				data, _ := os.ReadFile(d.unit)
				if string(data) != "unrelated unit" {
					t.Fatal("unrelated unit overwritten")
				}
			}
		})
	}
}
func TestLoginDeploymentUninstallFailureRestoresService(t *testing.T) {
	d, request, systemd, _ := fixtureLoginDeployment(t)
	if err := d.manage(context.Background(), request); err != nil {
		t.Fatal(err)
	}
	unit, _ := os.ReadFile(d.unit)
	systemd.fail = "daemon-reload"
	if d.manage(context.Background(), LoginServiceDeploymentRequest{Operation: "uninstall"}) == nil {
		t.Fatal("uninstall failure accepted")
	}
	restored, _ := os.ReadFile(d.unit)
	if !bytes.Equal(unit, restored) || !systemd.active || !systemd.enabled {
		t.Fatal("uninstall failure lost service")
	}
	if record, err := d.readRecord(); err != nil || record == nil {
		t.Fatal("uninstall failure lost installation")
	}
}

func TestLoginDeploymentEnsureRebindsStoppedRuntime(t *testing.T) {
	d, request, systemd, _ := fixtureLoginDeployment(t)
	if err := d.manage(context.Background(), request); err != nil {
		t.Fatal(err)
	}
	systemd.active = false
	request.Operation = "ensure"
	request.RuntimeSHA256 = strings.Repeat("b", 64)
	if err := d.manage(context.Background(), request); err != nil {
		t.Fatalf("ensure stopped runtime: %v", err)
	}
	record, err := d.readRecord()
	if err != nil || record.Request.RuntimeSHA256 != request.RuntimeSHA256 || !systemd.active {
		t.Fatal("ensure retained the old runtime identity")
	}
}
func TestLoginDeploymentEnsureActiveIsIdempotent(t *testing.T) {
	d, request, systemd, _ := fixtureLoginDeployment(t)
	if err := d.manage(context.Background(), request); err != nil {
		t.Fatal(err)
	}
	systemd.calls = nil
	request.Operation = "ensure"
	if err := d.manage(context.Background(), request); err != nil {
		t.Fatal(err)
	}
	for _, call := range systemd.calls {
		if strings.HasPrefix(call, "stop ") {
			t.Fatal("unchanged ensure interrupted a connection")
		}
	}
}

func TestLoginDeploymentRepairsOwnedCurrentTree(t *testing.T) {
	d, request, _, _ := fixtureLoginDeployment(t)
	if err := d.manage(t.Context(), request); err != nil {
		t.Fatal(err)
	}
	record, _ := d.readRecord()
	worker := filepath.Join(d.root, "versions", record.Digest, "desktop-drm")
	if err := os.Remove(worker); err != nil {
		t.Fatal(err)
	}
	if err := d.manage(t.Context(), request); err != nil {
		t.Fatal(err)
	}
	if !loginInstalledDigestMatches(worker, request.WorkerSHA256) {
		t.Fatal("current owned tree not repaired")
	}
}
func TestLoginDeploymentRecoversBeforeReadingRemovedRoot(t *testing.T) {
	d, request, systemd, _ := fixtureLoginDeployment(t)
	if err := d.manage(t.Context(), request); err != nil {
		t.Fatal(err)
	}
	original, _ := os.ReadFile(filepath.Join(d.root, "installed.json"))
	snapshot, err := d.snapshot(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	snapshot.Removing = true
	if err := d.saveSnapshot(snapshot); err != nil {
		t.Fatal(err)
	}
	if err := d.uninstall(t.Context(), &loginDeploymentRecord{}); err != nil {
		t.Fatal(err)
	}
	// Simulate a manager crash after rename. The next operation restores first.
	if err := d.manage(t.Context(), request); err != nil {
		t.Fatal(err)
	}
	after, _ := os.ReadFile(filepath.Join(d.root, "installed.json"))
	if !bytes.Equal(original, after) || !systemd.active || !systemd.enabled {
		t.Fatal("crash recovery lost original policy or activation")
	}
	if _, err := os.Stat(d.journal()); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("recovery retained transaction")
	}
}
func TestLoginDeploymentFutureAndUnknownRecordsAreReadOnly(t *testing.T) {
	for _, alteration := range []string{"future", "unknown", "duplicate", "extra"} {
		t.Run(alteration, func(t *testing.T) {
			d, request, systemd, _ := fixtureLoginDeployment(t)
			if err := d.manage(t.Context(), request); err != nil {
				t.Fatal(err)
			}
			path := filepath.Join(d.root, "installed.json")
			data, _ := os.ReadFile(path)
			switch alteration {
			case "future":
				data = bytes.Replace(data, []byte("\"version\":3"), []byte("\"version\":4"), 1)
			case "unknown":
				data = bytes.Replace(data, []byte(loginDeploymentKind), []byte("unknown_kind"), 1)
			case "duplicate":
				data = append([]byte("{\"version\":3,"), data[1:]...)
			case "extra":
				data = append([]byte("{\"extra\":true,"), data[1:]...)
			}
			if err := os.WriteFile(path, data, 0600); err != nil {
				t.Fatal(err)
			}
			systemd.calls = nil
			if err := d.manage(t.Context(), request); err == nil {
				t.Fatal("invalid record accepted")
			}
			after, _ := os.ReadFile(path)
			if !bytes.Equal(data, after) || len(systemd.calls) != 0 {
				t.Fatal("invalid record was modified")
			}
		})
	}
}
func TestLoginDeploymentRejectsRetiredVerbs(t *testing.T) {
	for _, op := range []string{"install", "update", "start"} {
		d, request, systemd, _ := fixtureLoginDeployment(t)
		request.Operation = op
		if err := d.manage(t.Context(), request); err == nil || len(systemd.calls) != 0 {
			t.Fatal("retired verb accepted", op)
		}
	}
}

func TestLoginDeploymentControlledProofFailureRestoresOriginal(t *testing.T) {
	if os.Geteuid() != 0 {
		t.Skip("controlled Runtime fixture requires isolated root")
	}
	d, request, systemd, _ := fixtureLoginDeployment(t)
	if err := d.manage(t.Context(), request); err != nil {
		t.Fatal(err)
	}
	original, _ := os.ReadFile(filepath.Join(d.root, "installed.json"))
	runtime := exec.Command("/bin/sleep", "30")
	runtime.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: 1000, Gid: 1000}}
	if err := runtime.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = runtime.Process.Kill(); _ = runtime.Wait() }()
	request.RuntimePID = uint32(runtime.Process.Pid)
	request.RuntimeStart, _ = loginProcessStart(request.RuntimePID)
	data, _ := os.ReadFile("/bin/sleep")
	hash := sha256.Sum256(data)
	request.RuntimeSHA256 = hex.EncodeToString(hash[:])
	request.OperationID = loginChallenge()
	request.Version = LoginServiceManagementVersion
	verified := false
	d.control = func(_ context.Context, event LoginServiceDeploymentEvent) error {
		if event.Stage == "confirmation_required" {
			return nil
		}
		verified = true
		if event.PolicyDigest != loginPolicyDigest(request.policy()) || event.OperationID != request.OperationID {
			t.Fatal("unbound proof challenge")
		}
		return errors.New("fixture rejected actual Runtime authentication")
	}
	if err := d.manage(t.Context(), request); err == nil {
		t.Fatal("failed proof committed")
	} else {
		t.Log("controlled result", err)
	}
	after, _ := os.ReadFile(filepath.Join(d.root, "installed.json"))
	if !verified || !bytes.Equal(original, after) || !systemd.active {
		t.Fatal("authentication failure did not restore original")
	}
}

func TestLoginDeploymentRecoversPartiallyCollectedCommit(t *testing.T) {
	d, _, _, _ := fixtureLoginDeployment(t)
	retired := d.root + ".committed"
	if err := os.Mkdir(retired, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(retired, "partial"), []byte("retired"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := d.recover(t.Context()); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(retired); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("retired committed data retained")
	}
}
