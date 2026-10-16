//go:build linux

package hostdesktop

import (
	"bufio"
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"syscall"
	"time"
)

type deploymentError string

func (e deploymentError) Error() string { return string(e) }
func loginDeploymentCode(err error) string {
	var code deploymentError
	if errors.As(err, &code) {
		return string(code)
	}
	if errors.Is(err, context.Canceled) {
		return "canceled"
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return "operation_timeout"
	}
	return "deployment_failed"
}
func loginChallenge() string { var b [16]byte; _, _ = rand.Read(b[:]); return hex.EncodeToString(b[:]) }
func (d *loginDeployer) checkRuntime(r LoginServiceDeploymentRequest) error {
	if r.RuntimePID == 0 || r.RuntimeStart == "" {
		return deploymentError("runtime_identity_changed")
	}
	start, err := loginProcessStart(r.RuntimePID)
	if err != nil || start != r.RuntimeStart {
		return deploymentError("runtime_identity_changed")
	}
	var st syscall.Stat_t
	if syscall.Stat(loginProcessPath(r.RuntimePID, ""), &st) != nil || st.Uid != r.RuntimeUID || st.Gid != r.RuntimeGID || !loginRuntimeDigestMatches(r.RuntimePID, r.RuntimeSHA256) {
		return deploymentError("runtime_identity_changed")
	}
	after, err := loginProcessStart(r.RuntimePID)
	if err != nil || after != start {
		return deploymentError("runtime_identity_changed")
	}
	return nil
}
func (d *loginDeployer) challenge(ctx context.Context, stage, digest string, count *int, generation string) error {
	event := LoginServiceDeploymentEvent{Stage: stage, OperationID: d.request.OperationID, ChallengeID: loginChallenge(), PolicyDigest: digest, ActiveConnections: count, ServiceGeneration: generation}
	if d.control == nil {
		return deploymentError("administrator_confirmation_required")
	}
	deadline := 10 * time.Second
	if stage == "confirmation_required" {
		deadline = 2 * time.Minute
	}
	wait, cancel := context.WithTimeout(ctx, deadline)
	defer cancel()
	return d.control(wait, event)
}
func (d *loginDeployer) maintain(ctx context.Context, disruptive bool) (func(), error) {
	noop := func() {}
	if !disruptive {
		return noop, nil
	}
	active := d.systemctl(ctx, "is-active", "--quiet", loginServiceUnit) == nil
	if !active {
		return noop, nil
	}
	// Unit tests inject systemd without a production daemon. All real managers
	// must acquire the root-only lease or obtain explicit unknown-impact consent.
	if d.control == nil && d.driverFile == "" {
		return noop, nil
	}
	conn, status, err := loginAcquireMaintenance(ctx)
	var count *int
	generation := ""
	if status != nil {
		count = &status.ActiveConnections
		generation = status.Generation
	}
	release := func() {
		if conn != nil {
			_ = conn.Close()
		}
	}
	if err != nil || count == nil || *count > 0 {
		if err = d.challenge(ctx, "confirmation_required", loginPolicyDigest(d.request.policy()), count, generation); err != nil {
			release()
			return noop, err
		}
	}
	if conn != nil {
		_ = conn.SetDeadline(time.Now().Add(5 * time.Second))
		var reply loginServiceReply
		if loginWritePacket(conn, loginServiceHello{Version: loginAttachmentVersion, Operation: "maintenance_check"}) != nil || loginReadPacket(bufio.NewReader(conn), &reply) != nil || reply.Code != "MAINTENANCE_HELD" {
			release()
			return noop, deploymentError("service_generation_changed")
		}
		_ = conn.SetDeadline(time.Time{})
	}
	return release, nil
}
func (d *loginDeployer) verify(ctx context.Context, policy LoginServicePolicy) error {
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if d.control == nil {
		return nil
	}
	if err := d.checkRuntime(d.request); err != nil {
		return err
	}
	digest := loginPolicyDigest(policy)
	if err := d.challenge(ctx, "verification_required", digest, nil, ""); err != nil {
		return err
	}
	return d.checkRuntime(d.request)
}

type loginDeploymentSnapshot struct {
	Kind                                          string
	Version                                       int
	RootExisted, VersionsExisted, Active, Enabled bool
	RootMode, VersionsMode                        uint32
	DriverWasLoaded                               bool
	Unit, Policy, Driver                          []byte
	UnitExisted, PolicyExisted, DriverExisted     bool
	Published                                     string
	Replace                                       bool
	Removing                                      bool
}

func (d *loginDeployer) journal() string { return d.root + ".transaction" }
func loginReadOptional(path string) ([]byte, bool, error) {
	if path == "" {
		return nil, false, nil
	}
	b, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, false, nil
	}
	return b, err == nil, err
}
func (d *loginDeployer) snapshot(ctx context.Context) (*loginDeploymentSnapshot, error) {
	s := &loginDeploymentSnapshot{Kind: "floe_host_desktop_transaction_v1", Version: 1}
	var err error
	if d.driverLoaded != nil {
		s.DriverWasLoaded = d.driverLoaded()
	}
	if _, e := os.Stat(filepath.Join(d.root, "installed.json")); e == nil {
		s.Active = d.systemctl(ctx, "is-active", "--quiet", loginServiceUnit) == nil
		s.Enabled = d.systemctl(ctx, "is-enabled", "--quiet", loginServiceUnit) == nil
	}
	if st, e := os.Lstat(d.root); e == nil {
		s.RootExisted = true
		s.RootMode = uint32(st.Mode().Perm())
	} else if !errors.Is(e, os.ErrNotExist) {
		return nil, e
	}
	if st, e := os.Lstat(filepath.Join(d.root, "versions")); e == nil {
		s.VersionsExisted = true
		s.VersionsMode = uint32(st.Mode().Perm())
	} else if !errors.Is(e, os.ErrNotExist) {
		return nil, e
	}
	if s.Unit, s.UnitExisted, err = loginReadOptional(d.unit); err != nil {
		return nil, err
	}
	if s.Policy, s.PolicyExisted, err = loginReadOptional(filepath.Join(d.root, "installed.json")); err != nil {
		return nil, err
	}
	if s.Driver, s.DriverExisted, err = loginReadOptional(d.driverFile); err != nil {
		return nil, err
	}
	stage, err := os.MkdirTemp(filepath.Dir(d.root), ".desktop-transaction-")
	if err != nil {
		return nil, err
	}
	defer loginRemoveDurable(stage)
	data, err := json.Marshal(s)
	if err != nil {
		return nil, err
	}
	if err = loginWriteAtomic(filepath.Join(stage, "state.json"), data, 0600); err != nil {
		return nil, err
	}
	if _, err = os.Lstat(d.journal()); !errors.Is(err, os.ErrNotExist) {
		return nil, deploymentError("transaction_recovery_required")
	}
	if err = loginRenameDurable(stage, d.journal()); err != nil {
		return nil, err
	}
	if err = loginSyncDirectory(filepath.Dir(d.root)); err != nil {
		return nil, err
	}
	return s, nil
}
func (d *loginDeployer) saveSnapshot(s *loginDeploymentSnapshot) error {
	b, err := json.Marshal(s)
	if err != nil {
		return err
	}
	return loginWriteAtomic(filepath.Join(d.journal(), "state.json"), b, 0600)
}
func loginRestoreFile(path string, data []byte, existed bool, mode os.FileMode) error {
	if path == "" {
		return nil
	}
	if existed {
		return loginWriteAtomic(path, data, mode)
	}
	return loginRemoveDurable(path)
}
func (d *loginDeployer) restore(ctx context.Context, s *loginDeploymentSnapshot) error {
	var result error
	if _, err := os.Lstat(d.unit); err == nil {
		result = errors.Join(result, d.systemctl(ctx, "stop", loginServiceUnit))
	}
	removed := d.root + ".removing"
	if _, err := os.Lstat(removed); err == nil && s.Removing {
		if _, e := os.Lstat(d.root); errors.Is(e, os.ErrNotExist) {
			result = errors.Join(result, loginRenameDurable(removed, d.root))
		} else {
			return deploymentError("transaction_recovery_failed")
		}
	}
	if s.Published != "" {
		target := filepath.Join(d.root, "versions", s.Published)
		backup := filepath.Join(d.journal(), "previous-version")
		if s.Replace {
			if _, err := os.Lstat(backup); err == nil {
				result = errors.Join(result, loginRemoveDurable(target), loginRenameDurable(backup, target))
			}
		} else {
			result = errors.Join(result, loginRemoveDurable(target))
		}
	}
	if s.RootExisted {
		result = errors.Join(result, os.MkdirAll(d.root, 0755))
	}
	if !s.Enabled {
		if _, err := os.Lstat(d.unit); err == nil {
			result = errors.Join(result, d.systemctl(ctx, "disable", loginServiceUnit))
		}
	}
	result = errors.Join(result, loginRestoreFile(d.unit, s.Unit, s.UnitExisted, 0644), d.systemctl(ctx, "daemon-reload"))
	if s.RootExisted {
		result = errors.Join(result, loginRestoreFile(filepath.Join(d.root, "installed.json"), s.Policy, s.PolicyExisted, 0600))
	}
	result = errors.Join(result, loginRestoreFile(d.driverFile, s.Driver, s.DriverExisted, 0644))
	operation := "disable"
	if s.Enabled {
		operation = "enable"
	}
	if s.UnitExisted && s.Enabled {
		result = errors.Join(result, d.systemctl(ctx, operation, loginServiceUnit))
	}
	if s.Active {
		result = errors.Join(result, d.systemctl(ctx, "start", loginServiceUnit))
	}
	if !s.DriverWasLoaded && d.driverLoaded != nil && d.driverLoaded() {
		result = errors.Join(result, d.loadDriver(ctx, true))
	}
	if !s.RootExisted {
		result = errors.Join(result, loginRemoveDurable(d.root))
	} else {
		versions := filepath.Join(d.root, "versions")
		if s.VersionsExisted {
			result = errors.Join(result, os.Chmod(versions, os.FileMode(s.VersionsMode)))
		} else {
			err := os.Remove(versions)
			if !errors.Is(err, os.ErrNotExist) {
				result = errors.Join(result, err)
			}
		}
		result = errors.Join(result, os.Chmod(d.root, os.FileMode(s.RootMode)))
	}
	return result
}
func (d *loginDeployer) rollback(s *loginDeploymentSnapshot, cause error) error {
	d.emit("rolling_back")
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	err := d.restore(ctx, s)
	outcome := "complete"
	if err != nil {
		outcome = "failed"
	} else {
		err = d.commitJournal(s)
		if err != nil {
			outcome = "failed"
		}
	}
	if d.report != nil {
		d.report(LoginServiceDeploymentEvent{Stage: "rolled_back", Rollback: outcome})
	}
	if err != nil {
		return errors.Join(cause, deploymentError("transaction_recovery_failed"))
	}
	return cause
}
func (d *loginDeployer) commitJournal(s *loginDeploymentSnapshot) error {
	// An atomic marker makes interrupted post-commit garbage collection distinct
	// from an uncommitted mutation. Recovery never rolls back a committed result.
	if err := loginWriteAtomic(filepath.Join(d.journal(), "committed"), []byte("1\n"), 0600); err != nil {
		return err
	}
	if s.Removing {
		if err := loginRemoveDurable(d.root + ".removing"); err != nil {
			return err
		}
	}
	// Atomic retirement preserves recovery authority until the commit is durable.
	if err := loginRenameDurable(d.journal(), d.root+".committed"); err != nil {
		return err
	}
	return d.cleanCommitted()
}
func (d *loginDeployer) cleanCommitted() error {
	path := d.root + ".committed"
	st, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil || !st.IsDir() || st.Mode().Perm() != 0700 || st.Sys().(*syscall.Stat_t).Uid != uint32(os.Geteuid()) {
		return deploymentError("transaction_recovery_failed")
	}
	return loginRemoveDurable(path)
}
func (d *loginDeployer) recover(ctx context.Context) error {
	if err := d.cleanCommitted(); err != nil {
		return err
	}
	dir := d.journal()
	st, err := os.Lstat(dir)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil || !st.IsDir() || st.Mode().Perm() != 0700 || st.Sys().(*syscall.Stat_t).Uid != uint32(os.Geteuid()) {
		return deploymentError("transaction_recovery_failed")
	}
	b, err := os.ReadFile(filepath.Join(dir, "state.json"))
	if err != nil || len(b) > 1<<20 {
		return deploymentError("transaction_recovery_failed")
	}
	var s loginDeploymentSnapshot
	if !hostDesktopUniqueJSON(b) || loginSnapshotDecode(b, &s) != nil || s.Kind != "floe_host_desktop_transaction_v1" || s.Version != 1 || (s.Published != "" && !validLoginDigest(s.Published)) {
		return deploymentError("transaction_recovery_failed")
	}
	if marker, err := os.ReadFile(filepath.Join(dir, "committed")); err == nil {
		if string(marker) != "1\n" {
			return deploymentError("transaction_recovery_failed")
		}
		return d.commitJournal(&s)
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	release, err := d.maintain(ctx, true)
	if err != nil {
		return err
	}
	defer release()
	d.emit("recovering")
	if err := d.rollback(&s, nil); err != nil {
		return err
	}
	return nil
}

func loginSyncDirectory(path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	return f.Sync()
}

func loginSnapshotDecode(data []byte, snapshot *loginDeploymentSnapshot) error {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(snapshot); err != nil {
		return err
	}
	if decoder.Decode(new(any)) != io.EOF {
		return ErrHostDesktopProtocol
	}
	return nil
}

func loginRuntimeDigestMatches(pid uint32, expected string) bool {
	// /proc/PID/exe is a kernel-owned executable descriptor, intentionally a link.
	file, err := os.Open(loginProcessPath(pid, "exe"))
	if err != nil {
		return false
	}
	defer file.Close()
	hash := sha256.New()
	size, err := io.Copy(hash, io.LimitReader(file, (512<<20)+1))
	return err == nil && size > 0 && size <= 512<<20 && hex.EncodeToString(hash.Sum(nil)) == expected
}

// Rename and removal durability includes every parent whose directory entry changes.
func loginRenameDurable(source, destination string) error {
	if err := os.Rename(source, destination); err != nil {
		return err
	}
	err := loginSyncDirectory(filepath.Dir(destination))
	if filepath.Dir(source) != filepath.Dir(destination) {
		err = errors.Join(err, loginSyncDirectory(filepath.Dir(source)))
	}
	return err
}
func loginRemoveDurable(path string) error {
	if err := os.RemoveAll(path); err != nil {
		return err
	}
	return loginSyncDirectory(filepath.Dir(path))
}

func loginSyncTree(root string) error {
	return filepath.WalkDir(root, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			return loginSyncDirectory(path)
		}
		file, err := os.Open(path)
		if err != nil {
			return err
		}
		return errors.Join(file.Sync(), file.Close())
	})
}
