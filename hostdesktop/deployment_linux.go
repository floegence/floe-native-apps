//go:build linux

package hostdesktop

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"syscall"
	"time"

	"github.com/floegence/floe-native-apps/internal/servicearchive"
	"golang.org/x/sys/unix"
)

type loginDeploymentRecord struct {
	Kind    string             `json:"kind"`
	Version int                `json:"version"`
	Request LoginServicePolicy `json:"policy"`
	Digest  string             `json:"digest"`
	raw     []byte
}

const loginDRMLicenseSHA256 = "a6db0e06dbfbfcd2f0875c5790cccbf7cd20b7b4fef7947086926f127cde0052"

const loginServiceUnit = "redeven-desktop.service"
const loginDeploymentKind = "floe_host_desktop_service_v1"

var errLoginDeployment = errors.New("login-screen deployment failed")

type loginDeployer struct {
	root, unit    string
	driverFile    string
	loadDriver    func(context.Context, bool) error
	driverPresent func() bool
	driverLoaded  func() bool
	systemctl     func(context.Context, ...string) error
	health        func(context.Context) error
	report        func(LoginServiceDeploymentEvent)
	control       LoginServiceDeploymentControl
	request       LoginServiceDeploymentRequest
}

func (d *loginDeployer) emit(stage string) {
	if d.report != nil {
		d.report(LoginServiceDeploymentEvent{Stage: stage, OperationID: d.request.OperationID})
	}
}
func loginSystemctl(ctx context.Context, args ...string) error {
	command := exec.CommandContext(ctx, "/usr/bin/systemctl", args...)
	command.Env = []string{"PATH=/usr/bin:/bin", "LANG=C"}
	return command.Run()
}
func ManageLoginScreenServiceControlled(ctx context.Context, request LoginServiceDeploymentRequest, report func(LoginServiceDeploymentEvent), control LoginServiceDeploymentControl) (ServiceStatus, error) {
	if control == nil {
		return ServiceStatus{State: ServiceFailed, Reason: "management_protocol_invalid"}, deploymentError("management_protocol_invalid")
	}
	if os.Geteuid() != 0 {
		return ServiceStatus{State: ServiceAuthorization, Backend: "linux-drm-kms"}, ErrServiceAuthorization
	}
	fd, err := unix.Open("/run/redeven-desktop-deployment.lock", unix.O_CREAT|unix.O_NOFOLLOW|unix.O_CLOEXEC|unix.O_RDWR, 0600)
	if err != nil {
		return ServiceStatus{State: ServiceFailed, Reason: "deployment_lock_unavailable"}, errLoginDeployment
	}
	defer unix.Close(fd)
	var stat unix.Stat_t
	if unix.Fstat(fd, &stat) != nil || stat.Uid != 0 || stat.Mode&unix.S_IFMT != unix.S_IFREG {
		return ServiceStatus{State: ServiceFailed, Reason: "deployment_lock_unavailable"}, errLoginDeployment
	}
	if unix.Flock(fd, unix.LOCK_EX|unix.LOCK_NB) != nil {
		return ServiceStatus{State: ServiceFailed, Reason: "deployment_in_progress"}, errLoginDeployment
	}
	defer unix.Flock(fd, unix.LOCK_UN)
	d := loginDeployer{root: "/usr/lib/redeven-desktop", unit: "/etc/systemd/system/" + loginServiceUnit, systemctl: loginSystemctl, health: loginServiceHealth, report: report, driverFile: "/etc/modules-load.d/redeven-desktop.conf", loadDriver: loginLoadDriver, driverLoaded: func() bool { _, err := os.Stat("/sys/module/uinput/refcnt"); return err == nil }, driverPresent: loginDriverPresent}
	for _, directory := range []string{filepath.Dir(d.root), filepath.Dir(d.unit), filepath.Dir(d.driverFile)} {
		if !loginRootOwnedDirectory(directory) {
			return ServiceStatus{State: ServiceFailed, Reason: "deployment_path_untrusted"}, errLoginDeployment
		}
	}
	for _, path := range []string{d.root, d.unit, filepath.Join(d.root, "installed.json"), filepath.Join(d.root, "versions"), d.driverFile, d.root + ".removing"} {
		if info, err := os.Lstat(path); err == nil {
			var st unix.Stat_t
			if unix.Lstat(path, &st) != nil || st.Uid != 0 || info.Mode()&os.ModeSymlink != 0 || info.Mode().Perm()&0022 != 0 {
				return ServiceStatus{State: ServiceFailed, Reason: "deployment_path_untrusted"}, errLoginDeployment
			}
		} else if !errors.Is(err, os.ErrNotExist) {
			return ServiceStatus{State: ServiceFailed, Reason: "deployment_path_unavailable"}, errLoginDeployment
		}
	}
	d.control = control
	d.request = request
	if err = d.manage(ctx, request); err != nil {
		if report != nil {
			report(LoginServiceDeploymentEvent{Stage: "failed", Code: "DEPLOYMENT_FAILED"})
		}
		return ServiceStatus{State: ServiceFailed, Reason: loginDeploymentCode(err), Backend: "linux-drm-kms"}, err
	}
	state := ServiceActive
	if request.Operation == "stop" {
		state = ServiceStopped
	}
	if request.Operation == "uninstall" {
		state = ServiceNotInstalled
	}
	return ServiceStatus{State: state, Backend: "linux-drm-kms"}, nil
}
func validLoginDigest(digest string) bool {
	value, err := hex.DecodeString(digest)
	return err == nil && len(value) == 32 && hex.EncodeToString(value) == digest
}
func (d *loginDeployer) readRecord() (*loginDeploymentRecord, error) {
	data, err := os.ReadFile(filepath.Join(d.root, "installed.json"))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil || len(data) > 8192 {
		return nil, errLoginDeployment
	}
	var envelope struct {
		Kind    string          `json:"kind"`
		Version int             `json:"version"`
		Policy  json.RawMessage `json:"policy"`
		Digest  string          `json:"digest"`
	}
	if loginManagementDecode(data, &envelope) != nil || envelope.Kind != loginDeploymentKind || envelope.Version < 1 || envelope.Version > 3 {
		return nil, errLoginDeployment
	}
	var policy LoginServicePolicy
	var canonical []byte
	if envelope.Version < 3 {
		// Versions 1 and 2 hashed the original request shape, including operation:"".
		// Keep that exact historical shape until the atomic version-3 migration.
		var legacy struct {
			Operation       string `json:"operation"`
			SourceDirectory string `json:"source_directory,omitempty"`
			RuntimeUID      uint32 `json:"runtime_uid,omitempty"`
			RuntimeGID      uint32 `json:"runtime_gid,omitempty"`
			RuntimeSHA256   string `json:"runtime_sha256,omitempty"`
			ServiceSHA256   string `json:"service_sha256,omitempty"`
			WorkerSHA256    string `json:"worker_sha256,omitempty"`
			MediaSHA256     string `json:"media_sha256,omitempty"`
		}
		if loginManagementDecode(envelope.Policy, &legacy) != nil || legacy.Operation != "" || legacy.SourceDirectory != "" {
			return nil, errLoginDeployment
		}
		canonical, _ = json.Marshal(legacy)
		policy = LoginServicePolicy{legacy.RuntimeUID, legacy.RuntimeGID, legacy.RuntimeSHA256, legacy.ServiceSHA256, legacy.WorkerSHA256, legacy.MediaSHA256}
	} else {
		if loginManagementDecode(envelope.Policy, &policy) != nil {
			return nil, errLoginDeployment
		}
		canonical, _ = json.Marshal(policy)
	}
	record := loginDeploymentRecord{Kind: envelope.Kind, Version: envelope.Version, Request: policy, Digest: envelope.Digest}
	if (record.Version == 1 && policy.MediaSHA256 != "") || (record.Version >= 2 && !validLoginDigest(policy.MediaSHA256)) || !validLoginDigest(record.Digest) || policy.RuntimeUID == 0 || !validLoginDigest(policy.RuntimeSHA256) || !validLoginDigest(policy.ServiceSHA256) || !validLoginDigest(policy.WorkerSHA256) {
		return nil, errLoginDeployment
	}
	digest := sha256.Sum256(canonical)
	if hex.EncodeToString(digest[:]) != record.Digest {
		return nil, errLoginDeployment
	}
	record.raw = append([]byte(nil), data...)
	return &record, nil
}
func (d *loginDeployer) manage(ctx context.Context, request LoginServiceDeploymentRequest) (result error) {
	if ctx.Err() != nil {
		return ctx.Err()
	}
	d.request = request
	var err error
	if request.Operation != "ensure" && request.Operation != "stop" && request.Operation != "uninstall" {
		return deploymentError("operation_invalid")
	}
	if d.control != nil {
		if request.Version != LoginServiceManagementVersion || request.OperationID == "" {
			return deploymentError("management_protocol_invalid")
		}
		if err = d.checkRuntime(request); err != nil {
			return err
		}
	}
	if err = d.recover(ctx); err != nil {
		return err
	}
	if _, err := os.Lstat(d.root + ".removing"); !errors.Is(err, os.ErrNotExist) {
		return deploymentError("installation_ownership_conflict")
	}
	record, err := d.readRecord()
	if err != nil {
		return deploymentError("installation_record_invalid")
	}
	if request.Operation == "ensure" {
		return d.install(ctx, request, record)
	}
	if record == nil {
		if request.Operation == "stop" {
			return ErrServiceNotInstalled
		}
		return d.uninstall(ctx, record)
	}
	release, err := d.maintain(ctx, true)
	if err != nil {
		return err
	}
	defer release()
	ctx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	undo, err := d.snapshot(ctx)
	if err != nil {
		return err
	}
	defer func() {
		if result != nil {
			result = d.rollback(undo, result)
		} else {
			result = d.commitJournal(undo)
		}
	}()
	if request.Operation == "stop" {
		d.emit("stopping_service")
		return d.systemctl(ctx, "stop", loginServiceUnit)
	}
	_, err = d.prepareDriver(ctx, "uninstall")
	if err != nil {
		return err
	}
	undo.Removing = true
	if err := d.saveSnapshot(undo); err != nil {
		return err
	}
	return d.uninstall(ctx, record)
}
func loginWriteAtomic(path string, data []byte, mode os.FileMode) error {
	file, err := os.CreateTemp(filepath.Dir(path), ".redeven-desktop-")
	if err != nil {
		return err
	}
	name := file.Name()
	defer os.Remove(name)
	if err = file.Chmod(mode); err == nil {
		_, err = file.Write(data)
	}
	if err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	if err = os.Rename(name, path); err != nil {
		return err
	}
	return loginSyncDirectory(filepath.Dir(path))
}
func loginCopyVerified(source, destination, expected string) error {
	if !validLoginDigest(expected) {
		return errLoginDeployment
	}
	fd, err := unix.Open(source, unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return err
	}
	input := os.NewFile(uintptr(fd), "deployment-source")
	defer input.Close()
	stat, err := input.Stat()
	if err != nil || !stat.Mode().IsRegular() || stat.Size() <= 0 || stat.Size() > servicearchive.MaxCompressedBytes {
		return errLoginDeployment
	}
	output, err := os.OpenFile(destination, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	hash := sha256.New()
	n, copyErr := io.Copy(io.MultiWriter(output, hash), io.LimitReader(input, servicearchive.MaxCompressedBytes+1))
	syncErr := output.Sync()
	closeErr := output.Close()
	if copyErr != nil || syncErr != nil || closeErr != nil || n != stat.Size() || hex.EncodeToString(hash.Sum(nil)) != expected {
		return errLoginDeployment
	}
	return os.Chmod(destination, 0755)
}
func (d *loginDeployer) unitContents(directory string, policy LoginServicePolicy) []byte {
	quote := strconv.Quote
	return []byte(fmt.Sprintf(`[Unit]
Description=Redeven physical remote desktop service
After=systemd-logind.service

[Service]
Type=simple
User=root
Group=%d
ExecStart=%s --worker %s --media-root %s --runtime-uid %d --runtime-gid %d --runtime-sha256 %s --policy-digest %s --deployment-journal %s
Restart=on-failure
RestartSec=2
RuntimeDirectory=redeven-desktop
RuntimeDirectoryMode=0750
UMask=0077
StandardInput=null
NoNewPrivileges=yes
AmbientCapabilities=CAP_SETUID CAP_SETGID
PrivateTmp=yes
ProtectHome=tmpfs
BindReadOnlyPaths=/run/user
ProtectSystem=strict
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_UNIX
CapabilityBoundingSet=CAP_SYS_ADMIN CAP_SYS_PTRACE CAP_DAC_OVERRIDE CAP_KILL CAP_SETUID CAP_SETGID CAP_CHOWN
LimitNOFILE=128
TimeoutStopSec=5

[Install]
WantedBy=multi-user.target
`, policy.RuntimeGID, quote(filepath.Join(directory, "floe-host-desktop-service")), quote(filepath.Join(directory, "desktop-drm")), quote(filepath.Join(directory, "media")), policy.RuntimeUID, policy.RuntimeGID, policy.RuntimeSHA256, loginPolicyDigest(policy), quote(d.journal())))
}
func (d *loginDeployer) install(ctx context.Context, request LoginServiceDeploymentRequest, old *loginDeploymentRecord) (result error) {
	policy := request.policy()
	if policy.RuntimeUID == 0 || !validLoginDigest(policy.RuntimeSHA256) || !validLoginDigest(policy.ServiceSHA256) || !validLoginDigest(policy.WorkerSHA256) || !validLoginDigest(policy.MediaSHA256) || !filepath.IsAbs(request.SourceDirectory) {
		return deploymentError("deployment_request_invalid")
	}
	oldUnit, unitErr := os.ReadFile(d.unit)
	if unitErr != nil && !errors.Is(unitErr, os.ErrNotExist) {
		return deploymentError("deployment_path_unavailable")
	}
	if old == nil && unitErr == nil {
		return deploymentError("installation_ownership_conflict")
	}
	if _, err := os.Lstat(d.root); err == nil && old == nil {
		return deploymentError("installation_ownership_conflict")
	}
	// Prepare on the installed filesystem before any service mutation.
	stage, err := os.MkdirTemp(filepath.Dir(d.root), ".desktop-preparation-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(stage)
	d.emit("verifying_files")
	for _, f := range []struct{ name, digest string }{{"floe-host-desktop-service", policy.ServiceSHA256}, {"desktop-drm", policy.WorkerSHA256}, {"libdrmtap.LICENSE", loginDRMLicenseSHA256}, {"media.tar.gz", policy.MediaSHA256}} {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if err = loginCopyVerified(filepath.Join(request.SourceDirectory, f.name), filepath.Join(stage, f.name), f.digest); err != nil {
			return deploymentError("artifact_verification_failed")
		}
	}
	if err = os.Chmod(filepath.Join(stage, "libdrmtap.LICENSE"), 0644); err != nil {
		return err
	}
	if err = os.Chmod(filepath.Join(stage, "media.tar.gz"), 0644); err != nil {
		return err
	}
	if err = loginExtractMedia(ctx, filepath.Join(stage, "media.tar.gz"), filepath.Join(stage, "media")); err != nil {
		return err
	}
	digest := loginPolicyDigest(policy)
	published := filepath.Join(d.root, "versions", digest)
	retained := false
	replace := false
	if _, err = os.Lstat(published); err == nil {
		retained = loginPreparedVersionMatches(ctx, stage, published)
		if !retained {
			if old == nil || old.Digest != digest {
				return deploymentError("retained_version_untrusted")
			}
			replace = true
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	active := old != nil && d.systemctl(ctx, "is-active", "--quiet", loginServiceUnit) == nil
	unchanged := old != nil && old.Version == 3 && old.Digest == digest && retained && string(oldUnit) == string(d.unitContents(published, policy))
	if unchanged && active {
		if d.health != nil {
			if err = d.health(ctx); err != nil {
				return err
			}
		}
		if err = d.verify(ctx, policy); err != nil {
			return err
		}
		d.emit("complete")
		return nil
	}
	release, err := d.maintain(ctx, active)
	if err != nil {
		return err
	}
	defer release()
	if d.control != nil {
		if err = d.checkRuntime(request); err != nil {
			return err
		}
	}
	ctx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	snapshot, err := d.snapshot(ctx)
	if err != nil {
		return err
	}
	defer func() {
		if result != nil {
			result = d.rollback(snapshot, result)
		} else if err := d.commitJournal(snapshot); err != nil {
			result = deploymentError("transaction_commit_unknown")
		} else {
			d.emit("complete")
		}
	}()
	_, err = d.prepareDriver(ctx, "ensure")
	if err != nil {
		return err
	}
	if err = os.MkdirAll(filepath.Join(d.root, "versions"), 0755); err != nil {
		return err
	}
	for _, path := range []string{d.root, filepath.Join(d.root, "versions")} {
		if err = os.Chmod(path, 0755); err != nil {
			return err
		}
	}
	if active {
		if err = d.systemctl(ctx, "stop", loginServiceUnit); err != nil {
			return err
		}
	}
	if !retained {
		snapshot.Published = digest
		snapshot.Replace = replace
		if err = d.saveSnapshot(snapshot); err != nil {
			return err
		}
		if replace {
			if err = os.Rename(published, filepath.Join(d.journal(), "previous-version")); err != nil {
				return err
			}
		}
		if err = os.Chmod(stage, 0755); err != nil {
			return err
		}
		if err = os.Rename(stage, published); err != nil {
			return err
		}
	}
	d.emit("installing_service")
	if err = loginWriteAtomic(d.unit, d.unitContents(published, policy), 0644); err != nil {
		return err
	}
	if err = d.systemctl(ctx, "daemon-reload"); err != nil {
		return err
	}
	if err = d.systemctl(ctx, "enable", loginServiceUnit); err != nil {
		return err
	}
	d.emit("starting_service")
	if err = d.systemctl(ctx, "start", loginServiceUnit); err != nil {
		return err
	}
	if err = d.systemctl(ctx, "is-active", "--quiet", loginServiceUnit); err != nil {
		return err
	}
	if d.health != nil {
		if err = d.health(ctx); err != nil {
			return err
		}
	}
	if err = d.verify(ctx, policy); err != nil {
		return err
	}
	record := loginDeploymentRecord{Kind: loginDeploymentKind, Version: 3, Request: policy, Digest: digest}
	data, _ := json.Marshal(record)
	if err = loginWriteAtomic(filepath.Join(d.root, "installed.json"), data, 0600); err != nil {
		return err
	}
	if ctx.Err() != nil {
		return ctx.Err()
	}
	return nil
}

func loginPreparedVersionMatches(ctx context.Context, prepared, installed string) bool {
	info, err := os.Lstat(installed)
	if err != nil || !info.IsDir() || info.Mode().Perm() != 0755 {
		return false
	}
	preparation, err := os.Lstat(prepared)
	if err != nil || info.Sys().(*syscall.Stat_t).Uid != preparation.Sys().(*syscall.Stat_t).Uid {
		return false
	}
	root, err := os.OpenRoot(installed)
	if err != nil {
		return false
	}
	defer root.Close()
	matched := 0
	err = fs.WalkDir(root.FS(), ".", func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if err = ctx.Err(); err != nil {
			return err
		}
		if path == "." {
			return nil
		}
		actual, err := entry.Info()
		if err != nil || actual.Mode().Perm()&0022 != 0 {
			return errLoginDeployment
		}
		expected, err := os.Lstat(filepath.Join(prepared, path))
		if err != nil || actual.IsDir() != expected.IsDir() || actual.Sys().(*syscall.Stat_t).Uid != expected.Sys().(*syscall.Stat_t).Uid {
			return errLoginDeployment
		}
		matched++
		if actual.IsDir() {
			if actual.Mode().Perm() != expected.Mode().Perm() {
				return errLoginDeployment
			}
			return nil
		}
		if !actual.Mode().IsRegular() || actual.Mode().Perm() != expected.Mode().Perm() || actual.Size() != expected.Size() {
			return errLoginDeployment
		}
		if actual.Size() == 0 {
			return nil
		}
		input, err := os.Open(filepath.Join(prepared, path))
		if err != nil {
			return err
		}
		hash := sha256.New()
		_, copyErr := io.Copy(hash, input)
		closeErr := input.Close()
		if copyErr != nil || closeErr != nil || !loginInstalledDigestMatches(filepath.Join(installed, path), hex.EncodeToString(hash.Sum(nil))) {
			return errLoginDeployment
		}
		return nil
	})
	if err != nil {
		return false
	}
	expected := 0
	err = filepath.WalkDir(prepared, func(path string, _ fs.DirEntry, err error) error {
		if path != prepared {
			expected++
		}
		return err
	})
	return err == nil && matched == expected && ctx.Err() == nil
}
func (d *loginDeployer) uninstall(ctx context.Context, record *loginDeploymentRecord) error {
	if record == nil {
		if _, err := os.Lstat(d.unit); err == nil {
			return deploymentError("installation_ownership_conflict")
		}
		return nil
	}
	d.emit("stopping_service")
	if err := d.systemctl(ctx, "stop", loginServiceUnit); err != nil {
		return err
	}
	if err := d.systemctl(ctx, "disable", loginServiceUnit); err != nil {
		return err
	}
	if ctx.Err() != nil {
		return ctx.Err()
	}
	removed := d.root + ".removing"
	if _, err := os.Lstat(removed); !errors.Is(err, os.ErrNotExist) {
		return deploymentError("transaction_recovery_required")
	}
	if err := os.Rename(d.root, removed); err != nil {
		return err
	}
	if err := os.Remove(d.unit); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := d.systemctl(ctx, "daemon-reload"); err != nil {
		return err
	}
	return ctx.Err()
}

func loginInstalledDigestMatches(path, expected string) bool {
	fd, err := unix.Open(path, unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return false
	}
	file := os.NewFile(uintptr(fd), "installed-service")
	defer file.Close()
	stat, err := file.Stat()
	if err != nil || !stat.Mode().IsRegular() || stat.Size() <= 0 || stat.Size() > 512<<20 {
		return false
	}
	hash := sha256.New()
	n, err := io.Copy(hash, io.LimitReader(file, (512<<20)+1))
	return err == nil && n == stat.Size() && hex.EncodeToString(hash.Sum(nil)) == expected
}

func loginRootOwnedDirectory(path string) bool {
	if !filepath.IsAbs(path) || path != filepath.Clean(path) {
		return false
	}
	for p := path; ; p = filepath.Dir(p) {
		var stat unix.Stat_t
		if unix.Lstat(p, &stat) != nil || stat.Uid != 0 || stat.Mode&unix.S_IFMT != unix.S_IFDIR || stat.Mode&0022 != 0 {
			return false
		}
		if p == "/" {
			return true
		}
	}
}
