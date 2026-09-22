//go:build linux

package nativeapps

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// InstallationForProcess identifies a legacy backend's private loader using
// kernel evidence. Nil means a system installation outside this manager's root.
// The caller still owns application authorization and backend identity checks.
func (m *Manager) InstallationForProcess(process ProcessIdentity) (*Installation, error) {
	if !process.Alive() {
		return nil, ErrInvalid
	}
	executable, err := os.Readlink("/proc/" + strconv.Itoa(process.PID) + "/exe")
	if err != nil {
		return nil, err
	}
	if !process.Alive() {
		return nil, ErrInvalid
	}
	root, err := filepath.EvalSymlinks(m.root)
	if err != nil {
		return nil, err
	}
	rel, err := filepath.Rel(filepath.Join(root, "packages"), executable)
	if err != nil || !filepath.IsLocal(rel) {
		return nil, nil
	}
	parts := strings.Split(rel, string(filepath.Separator))
	if len(parts) < 2 {
		return nil, ErrInvalid
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed {
		return nil, ErrInvalid
	}
	item, ok := m.installation(parts[0])
	if !ok {
		return nil, ErrInvalid
	}
	if _, err := m.installedDirectory(item.Digest); err != nil {
		return nil, err
	}
	item.Ready = true
	return &item, nil
}

// ObserveProcess reads the kernel's boot and start identity for a live process.
func ObserveProcess(pid int) (ProcessIdentity, error) {
	if pid <= 0 {
		return ProcessIdentity{}, ErrInvalid
	}
	boot, err := os.ReadFile("/proc/sys/kernel/random/boot_id")
	if err != nil {
		return ProcessIdentity{}, err
	}
	data, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/stat")
	if err != nil {
		return ProcessIdentity{}, err
	}
	started, err := processStart(string(data))
	if err != nil {
		return ProcessIdentity{}, err
	}
	return ProcessIdentity{PID: pid, Boot: strings.TrimSpace(string(boot)), Started: started}, nil
}

func processStart(stat string) (string, error) {
	end := strings.LastIndexByte(stat, ')')
	if end < 0 {
		return "", ErrInvalid
	}
	fields := strings.Fields(stat[end+1:])
	if len(fields) < 20 || fields[0] == "Z" || fields[0] == "X" {
		return "", fmt.Errorf("process is not live")
	}
	if _, err := strconv.ParseUint(fields[19], 10, 64); err != nil {
		return "", ErrInvalid
	}
	return fields[19], nil
}
