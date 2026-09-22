//go:build linux

package nativeapps

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

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
