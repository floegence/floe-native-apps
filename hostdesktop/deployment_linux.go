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
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"time"

	"github.com/floegence/floe-native-apps/internal/servicearchive"
	"golang.org/x/sys/unix"
)

type loginDeploymentRecord struct {
	Kind    string                        `json:"kind"`
	Version int                           `json:"version"`
	Request LoginServiceDeploymentRequest `json:"policy"`
	Digest  string                        `json:"digest"`
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
}

func (d *loginDeployer) emit(stage string) {
	if d.report != nil {
		d.report(LoginServiceDeploymentEvent{Stage: stage})
	}
}
func loginSystemctl(ctx context.Context, args ...string) error {
	command := exec.CommandContext(ctx, "/usr/bin/systemctl", args...)
	command.Env = []string{"PATH=/usr/bin:/bin", "LANG=C"}
	return command.Run()
}
func ManageLoginScreenService(ctx context.Context, request LoginServiceDeploymentRequest, report func(LoginServiceDeploymentEvent)) (ServiceStatus, error) {
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
	for _, path := range []string{d.root, d.unit, filepath.Join(d.root, "installed.json"), d.driverFile} {
		if info, err := os.Lstat(path); err == nil {
			var st unix.Stat_t
			if unix.Lstat(path, &st) != nil || st.Uid != 0 || info.Mode()&os.ModeSymlink != 0 || info.Mode().Perm()&0022 != 0 {
				return ServiceStatus{State: ServiceFailed, Reason: "deployment_path_untrusted"}, errLoginDeployment
			}
		} else if !errors.Is(err, os.ErrNotExist) {
			return ServiceStatus{State: ServiceFailed, Reason: "deployment_path_unavailable"}, errLoginDeployment
		}
	}
	if err = d.manage(ctx, request); err != nil {
		if report != nil {
			report(LoginServiceDeploymentEvent{Stage: "failed", Code: "DEPLOYMENT_FAILED"})
		}
		return ServiceStatus{State: ServiceFailed, Reason: "deployment_failed", Backend: "linux-drm-kms"}, errLoginDeployment
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
	var record loginDeploymentRecord
	if json.Unmarshal(data, &record) != nil || record.Kind != loginDeploymentKind || (record.Version != 1 && record.Version != 2) ||
		(record.Version == 1 && record.Request.MediaSHA256 != "") || (record.Version == 2 && !validLoginDigest(record.Request.MediaSHA256)) ||
		!validLoginDigest(record.Digest) || record.Request.Operation != "" || record.Request.SourceDirectory != "" || record.Request.RuntimeUID == 0 || !validLoginDigest(record.Request.RuntimeSHA256) || !validLoginDigest(record.Request.ServiceSHA256) || !validLoginDigest(record.Request.WorkerSHA256) {
		return nil, errLoginDeployment
	}
	policy, _ := json.Marshal(record.Request)
	digest := sha256.Sum256(policy)
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
	record, err := d.readRecord()
	if err != nil {
		return err
	}

	if request.Operation == "install" || request.Operation == "update" || request.Operation == "uninstall" {
		undo, driverErr := d.prepareDriver(ctx, request.Operation)
		defer func() {
			if result == nil {
				return
			}
			rollback, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			if undo != nil {
				if err := undo(rollback); err != nil {
					if d.report != nil {
						d.report(LoginServiceDeploymentEvent{Stage: "rolled_back", Rollback: "failed"})
					}
					result = errors.Join(result, errLoginDeployment)
				}
			}
		}()
		if driverErr != nil {
			return driverErr
		}
	}
	switch request.Operation {
	case "start", "stop":
		if record == nil {
			return ErrServiceNotInstalled
		}
		d.emit(request.Operation)
		wasActive := d.systemctl(ctx, "is-active", "--quiet", loginServiceUnit) == nil
		err := d.systemctl(ctx, request.Operation, loginServiceUnit)
		if err == nil && ctx.Err() == nil && request.Operation == "start" && d.health != nil {
			err = d.health(ctx)
		}
		if err == nil {
			err = ctx.Err()
		}
		if err != nil {
			rollback, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			operation := "stop"
			if wasActive {
				operation = "start"
			}
			d.emit("rolling_back")
			rollbackErr := d.systemctl(rollback, operation, loginServiceUnit)
			outcome := "complete"
			if rollbackErr != nil {
				outcome = "failed"
			}
			if d.report != nil {
				d.report(LoginServiceDeploymentEvent{Stage: "rolled_back", Rollback: outcome})
			}
		}
		return err
	case "install", "update":
		return d.install(ctx, request, record)
	case "uninstall":
		return d.uninstall(ctx, record)
	default:
		return errLoginDeployment
	}
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
	return os.Rename(name, path)
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
func (d *loginDeployer) unitContents(directory string, policy LoginServiceDeploymentRequest) []byte {
	quote := strconv.Quote
	return []byte(fmt.Sprintf(`[Unit]
Description=Redeven physical remote desktop service
After=systemd-logind.service

[Service]
Type=simple
User=root
Group=%d
ExecStart=%s --worker %s --media-root %s --runtime-uid %d --runtime-gid %d --runtime-sha256 %s
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
`, policy.RuntimeGID, quote(filepath.Join(directory, "floe-host-desktop-service")), quote(filepath.Join(directory, "desktop-drm")), quote(filepath.Join(directory, "media")), policy.RuntimeUID, policy.RuntimeGID, policy.RuntimeSHA256))
}
func (d *loginDeployer) install(ctx context.Context, request LoginServiceDeploymentRequest, old *loginDeploymentRecord) (result error) {
	if request.RuntimeUID == 0 || !validLoginDigest(request.RuntimeSHA256) || !validLoginDigest(request.ServiceSHA256) || !validLoginDigest(request.WorkerSHA256) || !validLoginDigest(request.MediaSHA256) || !filepath.IsAbs(request.SourceDirectory) {
		return errLoginDeployment
	}
	oldUnit, unitErr := os.ReadFile(d.unit)
	if unitErr != nil && !errors.Is(unitErr, os.ErrNotExist) {
		return errLoginDeployment
	}
	if old == nil && unitErr == nil {
		return errLoginDeployment
	} // Never adopt an unrelated unit.
	rootExisted := false
	var rootMode, versionsMode os.FileMode
	versions := filepath.Join(d.root, "versions")
	versionsExisted := false
	if info, err := os.Lstat(d.root); err == nil {
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return errLoginDeployment
		}
		rootExisted = true
		rootMode = info.Mode().Perm()
		if old == nil {
			return errLoginDeployment
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return errLoginDeployment
	}
	if info, err := os.Lstat(versions); err == nil {
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return errLoginDeployment
		}
		versionsExisted, versionsMode = true, info.Mode().Perm()
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	oldActive := old != nil && d.systemctl(ctx, "is-active", "--quiet", loginServiceUnit) == nil
	oldEnabled := old != nil && d.systemctl(ctx, "is-enabled", "--quiet", loginServiceUnit) == nil
	if err := os.MkdirAll(d.root, 0755); err != nil {
		return err
	}
	stage, err := os.MkdirTemp(d.root, ".transaction-")
	if err != nil {
		if !rootExisted {
			_ = os.Remove(d.root)
		}
		return err
	}
	defer os.RemoveAll(stage)
	staged := false
	unitChanged := false
	serviceChanged := false
	recordChanged := false
	var published string
	defer func() {
		if result == nil {
			return
		}
		// Cancellation must not cancel rollback. A bounded independent context
		// restores the exact previous unit, policy and activation state.
		rollback, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		d.emit("rolling_back")
		var rollbackErr error
		if serviceChanged {
			rollbackErr = errors.Join(rollbackErr, d.systemctl(rollback, "stop", loginServiceUnit))
		}
		if old == nil && serviceChanged {
			rollbackErr = errors.Join(rollbackErr, d.systemctl(rollback, "disable", loginServiceUnit))
		}
		if unitChanged {
			if unitErr == nil {
				rollbackErr = errors.Join(rollbackErr, loginWriteAtomic(d.unit, oldUnit, 0644))
			} else {
				if err := os.Remove(d.unit); err != nil && !errors.Is(err, os.ErrNotExist) {
					rollbackErr = errors.Join(rollbackErr, err)
				}
			}
			rollbackErr = errors.Join(rollbackErr, d.systemctl(rollback, "daemon-reload"))
		}
		if recordChanged {
			if old != nil {
				rollbackErr = errors.Join(rollbackErr, loginWriteAtomic(filepath.Join(d.root, "installed.json"), old.raw, 0600))
			} else {
				_ = os.Remove(filepath.Join(d.root, "installed.json"))
			}
		}
		if old != nil {
			operation := "disable"
			if oldEnabled {
				operation = "enable"
			}
			rollbackErr = errors.Join(rollbackErr, d.systemctl(rollback, operation, loginServiceUnit))
			if oldActive {
				rollbackErr = errors.Join(rollbackErr, d.systemctl(rollback, "start", loginServiceUnit))
			}
		}
		if staged && published != "" {
			rollbackErr = errors.Join(rollbackErr, os.RemoveAll(published))
		}
		if !rootExisted {
			_ = os.RemoveAll(d.root)
		} else {
			if versionsExisted {
				rollbackErr = errors.Join(rollbackErr, os.Chmod(versions, versionsMode))
			} else if err := os.Remove(versions); err != nil && !errors.Is(err, os.ErrNotExist) {
				rollbackErr = errors.Join(rollbackErr, err)
			}
			rollbackErr = errors.Join(rollbackErr, os.Chmod(d.root, rootMode))
		}
		outcome := "complete"
		if rollbackErr != nil {
			outcome = "failed"
			result = errors.Join(result, errLoginDeployment)
		}
		if d.report != nil {
			d.report(LoginServiceDeploymentEvent{Stage: "rolled_back", Rollback: outcome})
		}
	}()
	// SSH management uses umask 0077. The unprivileged converter needs explicit
	// traversal of its immutable executable paths; installed policy stays 0600.
	// A failed update restores the previous directory permissions.
	if err = os.Chmod(d.root, 0755); err != nil {
		return err
	}
	d.emit("verifying_files")
	for _, file := range []struct{ name, digest string }{{"floe-host-desktop-service", request.ServiceSHA256}, {"desktop-drm", request.WorkerSHA256}, {"libdrmtap.LICENSE", loginDRMLicenseSHA256}} {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if err = loginCopyVerified(filepath.Join(request.SourceDirectory, file.name), filepath.Join(stage, file.name), file.digest); err != nil {
			return err
		}
	}
	if err = os.Chmod(filepath.Join(stage, "libdrmtap.LICENSE"), 0644); err != nil {
		return err
	}
	archive := filepath.Join(stage, "media.tar.gz")
	if err = loginCopyVerified(filepath.Join(request.SourceDirectory, "media.tar.gz"), archive, request.MediaSHA256); err != nil {
		return err
	}
	if err = loginExtractMedia(ctx, archive, filepath.Join(stage, "media")); err != nil {
		return err
	}
	if err = os.Chmod(archive, 0644); err != nil {
		return err
	}
	if ctx.Err() != nil {
		return ctx.Err()
	}
	// Source paths are transfer locations, not installed policy or provenance.
	request.SourceDirectory = ""
	request.Operation = ""
	data, _ := json.Marshal(request)
	hash := sha256.Sum256(data)
	digest := hex.EncodeToString(hash[:])
	if err = os.MkdirAll(versions, 0755); err != nil {
		return err
	}
	if err = os.Chmod(versions, 0755); err != nil {
		return err
	}
	published = filepath.Join(versions, digest)
	if old != nil && old.Digest == digest {
		if !loginInstalledDigestMatches(filepath.Join(published, "media.tar.gz"), request.MediaSHA256) {
			return errLoginDeployment
		}
		for _, file := range []struct{ name, digest string }{{"floe-host-desktop-service", request.ServiceSHA256}, {"desktop-drm", request.WorkerSHA256}, {"libdrmtap.LICENSE", loginDRMLicenseSHA256}} {
			if !loginInstalledDigestMatches(filepath.Join(published, file.name), file.digest) {
				return errLoginDeployment
			}
		}
		if string(oldUnit) != string(d.unitContents(published, request)) {
			return errLoginDeployment
		}
		d.emit("starting_service")
		serviceChanged = true
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
		if ctx.Err() != nil {
			return ctx.Err()
		}
		d.emit("complete")
		return nil
	}
	if _, err = os.Lstat(published); err == nil {
		return errLoginDeployment
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err = os.Chmod(stage, 0755); err != nil {
		return err
	}
	if err = os.Rename(stage, published); err != nil {
		return err
	}
	staged = true
	d.emit("installing_service")
	if old != nil {
		serviceChanged = true
		if err = d.systemctl(ctx, "stop", loginServiceUnit); err != nil {
			return err
		}
	}
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if err = loginWriteAtomic(d.unit, d.unitContents(published, request), 0644); err != nil {
		return err
	}
	unitChanged = true
	if err = d.systemctl(ctx, "daemon-reload"); err != nil {
		return err
	}
	serviceChanged = true
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
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if d.health != nil {
		if err = d.health(ctx); err != nil {
			return err
		}
	}
	record := loginDeploymentRecord{Kind: loginDeploymentKind, Version: 2, Request: request, Digest: digest}
	data, _ = json.Marshal(record)
	if err = loginWriteAtomic(filepath.Join(d.root, "installed.json"), data, 0600); err != nil {
		return err
	}
	recordChanged = true
	if ctx.Err() != nil {
		return ctx.Err()
	}
	d.emit("complete")
	return nil
}
func (d *loginDeployer) uninstall(ctx context.Context, record *loginDeploymentRecord) error {
	if record == nil {
		if _, err := os.Lstat(d.unit); err == nil {
			return errLoginDeployment
		}
		return nil
	}
	// Preserve the installation until service stop and disable both succeed.
	// The directory is renamed atomically and restored if unit removal fails.
	oldUnit, err := os.ReadFile(d.unit)
	if err != nil {
		return err
	}
	active := d.systemctl(ctx, "is-active", "--quiet", loginServiceUnit) == nil
	enabled := d.systemctl(ctx, "is-enabled", "--quiet", loginServiceUnit) == nil
	d.emit("stopping_service")
	if err = d.systemctl(ctx, "stop", loginServiceUnit); err != nil {
		return err
	}
	restore := func() {
		d.emit("rolling_back")
		rollback, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		rollbackErr := loginWriteAtomic(d.unit, oldUnit, 0644)
		rollbackErr = errors.Join(rollbackErr, d.systemctl(rollback, "daemon-reload"))
		if enabled {
			rollbackErr = errors.Join(rollbackErr, d.systemctl(rollback, "enable", loginServiceUnit))
		}
		if active {
			rollbackErr = errors.Join(rollbackErr, d.systemctl(rollback, "start", loginServiceUnit))
		}
		outcome := "complete"
		if rollbackErr != nil {
			outcome = "failed"
		}
		if d.report != nil {
			d.report(LoginServiceDeploymentEvent{Stage: "rolled_back", Rollback: outcome})
		}
	}
	if err = d.systemctl(ctx, "disable", loginServiceUnit); err != nil {
		restore()
		return err
	}
	if ctx.Err() != nil {
		restore()
		return ctx.Err()
	}
	removed := d.root + ".removing"
	if _, err = os.Lstat(removed); !errors.Is(err, os.ErrNotExist) {
		restore()
		return errLoginDeployment
	}
	if err = os.Rename(d.root, removed); err != nil {
		restore()
		return err
	}
	if err = os.Remove(d.unit); err != nil {
		_ = os.Rename(removed, d.root)
		restore()
		return err
	}
	if err = d.systemctl(ctx, "daemon-reload"); err != nil {
		_ = os.Rename(removed, d.root)
		restore()
		return err
	}
	if ctx.Err() != nil {
		_ = os.Rename(removed, d.root)
		restore()
		return ctx.Err()
	}
	if err = os.RemoveAll(removed); err != nil {
		return err
	}
	d.emit("complete")
	return nil
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
