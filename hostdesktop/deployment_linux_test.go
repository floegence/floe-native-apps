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
	request := LoginServiceDeploymentRequest{Operation: "install", SourceDirectory: source, RuntimeUID: 1000, RuntimeGID: 1000, RuntimeSHA256: strings.Repeat("a", 64), ServiceSHA256: digest("floe-host-desktop-service"), WorkerSHA256: digest("desktop-drm")}
	var media bytes.Buffer
	compressed := gzip.NewWriter(&media)
	archive := tar.NewWriter(compressed)
	for _, name := range []string{"floe/host-desktop/python3", "floe/host-desktop/host_desktop_drm.py", "floe/host-desktop/host_desktop_login_text.py", "usr/bin/python3"} {
		data := []byte("media fixture")
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
	if record.Request.SourceDirectory != "" || record.Request.Operation != "" {
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
	request.Operation = "update"
	if err = d.manage(context.Background(), request); err != nil {
		t.Fatal(err)
	}
	if err = d.manage(context.Background(), LoginServiceDeploymentRequest{Operation: "stop"}); err != nil {
		t.Fatal(err)
	}
	if systemd.active {
		t.Fatal("stop retained service")
	}
	if err = d.manage(context.Background(), LoginServiceDeploymentRequest{Operation: "start"}); err != nil {
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
	request.Operation, request.RuntimeSHA256 = "update", strings.Repeat("b", 64)
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
	request.Operation = "update"
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
