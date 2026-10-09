//go:build linux

package hostdesktop

import (
	"bufio"
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
	"time"
)

// Opt-in qualification owns the real unit only when no prior installation exists.
// A release qualifier must supply the reviewed kit and explicitly authorize root.
func TestLoginDeploymentRealSystemd(t *testing.T) {
	source := os.Getenv("FLOE_LOGIN_DEPLOYMENT_KIT")
	if source == "" {
		t.Skip("real systemd deployment not requested")
	}
	if os.Geteuid() != 0 {
		t.Fatal("administrator authorization required")
	}
	previousUmask := syscall.Umask(0077)
	defer syscall.Umask(previousUmask)
	for _, name := range []string{"/etc/systemd/system/redeven-desktop.service", "/usr/lib/redeven-desktop", "/etc/modules-load.d/redeven-desktop.conf"} {
		if _, err := os.Lstat(name); !errors.Is(err, os.ErrNotExist) {
			t.Fatal("qualifier refuses an existing installation")
		}
	}
	digest := func(path string) string {
		t.Helper()
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		hash := sha256.Sum256(data)
		return hex.EncodeToString(hash[:])
	}
	binary, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	request := LoginServiceDeploymentRequest{Operation: "install", SourceDirectory: source, RuntimeUID: 1000, RuntimeGID: 1000, RuntimeSHA256: digest(binary), ServiceSHA256: digest(filepath.Join(source, "floe-host-desktop-service")), WorkerSHA256: digest(filepath.Join(source, "desktop-drm")), MediaSHA256: digest(filepath.Join(source, "media.tar.gz"))}
	report := func(event LoginServiceDeploymentEvent) {
		t.Logf("deployment stage=%s code=%s rollback=%s", event.Stage, event.Code, event.Rollback)
	}
	defer func() {
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		_, err := ManageLoginScreenService(ctx, LoginServiceDeploymentRequest{Operation: "uninstall"}, report)
		if err != nil {
			t.Error("qualification cleanup failed")
		}
	}()
	var canceledRollback string
	ctx, cancel := context.WithCancel(context.Background())
	_, err = ManageLoginScreenService(ctx, request, func(event LoginServiceDeploymentEvent) {
		report(event)
		if event.Stage == "rolled_back" {
			canceledRollback = event.Rollback
		}
		if event.Stage == "starting_service" {
			cancel()
		}
	})
	cancel()
	if err == nil {
		t.Fatal("canceled transaction accepted")
	}
	if canceledRollback != "complete" {
		t.Fatal("canceled rollback did not complete")
	}
	for _, name := range []string{"/etc/systemd/system/redeven-desktop.service", "/usr/lib/redeven-desktop", "/etc/modules-load.d/redeven-desktop.conf"} {
		if _, err := os.Lstat(name); !errors.Is(err, os.ErrNotExist) {
			t.Fatal("cancel retained a partial installation")
		}
	}
	status, err := ManageLoginScreenService(context.Background(), request, report)
	if err != nil || status.State != ServiceActive {
		t.Fatalf("install failed: %+v %v", status, err)
	}
	qualifyUnlock := os.Getenv("FLOE_LOGIN_QUALIFY_UNLOCK") == "1"
	if qualifyUnlock {
		power := exec.Command("/usr/bin/busctl", "--address=unix:path=/run/user/1000/bus", "set-property", "org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig", "org.gnome.Mutter.DisplayConfig", "PowerSaveMode", "i", "3")
		power.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: 1000, Gid: 1000}}
		if power.Run() != nil {
			t.Fatal("sleeping-display fixture unavailable")
		}
		t.Log("display sleeping before the unprivileged attachment; service must wake it without input")
	}
	client := func() {
		t.Helper()
		name := "^TestLoginServiceQualificationClient$"
		if qualifyUnlock {
			name = "^TestLoginUnlockQualificationClient$"
			qualifyUnlock = false
		}
		command := exec.Command(binary, "-test.run", name, "-test.v")
		command.Stdin = os.Stdin
		command.Env = append(os.Environ(), "FLOE_LOGIN_SERVICE_CLIENT_SOCKET="+LoginServiceSocket)
		if os.Getenv("FLOE_LOGIN_EXPECT_DISCONNECTED") == "1" {
			command.Env = append(command.Env, "FLOE_LOGIN_SERVICE_CLIENT_NO_SCANOUT=1")
		}
		command.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: 1000, Gid: 1000, Groups: []uint32{1000}}}
		output, err := command.CombinedOutput()
		t.Log(string(output))
		if err != nil {
			t.Fatal("installed daemon attachment failed")
		}
	}
	client()
	// Keep a real, acknowledged key down across each externally initiated
	// lifetime boundary. Disconnect-before-stop cannot prove this contract.
	boundaries := []string{"service_stop", "runtime_exit"}
	if os.Getenv("FLOE_LOGIN_QUALIFY_SWITCH") == "1" {
		boundaries = append(boundaries, "user_switch")
	}
	for _, boundary := range boundaries {
		command := exec.Command(binary, "-test.run", "^TestLoginServiceQualificationClient$", "-test.v")
		command.Env = append(os.Environ(), "FLOE_LOGIN_SERVICE_CLIENT_SOCKET="+LoginServiceSocket, "FLOE_LOGIN_HOLD_INPUT=1")
		command.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: 1000, Gid: 1000, Groups: []uint32{1000}}}
		output, err := command.StdoutPipe()
		if err != nil || command.Start() != nil {
			t.Fatal("held client start failed")
		}
		ready := make(chan string, 1)
		go func() {
			scanner := bufio.NewScanner(output)
			var lines []string
			for scanner.Scan() {
				if scanner.Text() == "qualification_input_held" {
					ready <- ""
					return
				}
				lines = append(lines, scanner.Text())
			}
			ready <- strings.Join(lines, "\n")
		}()
		select {
		case output := <-ready:
			if output != "" {
				_ = command.Process.Kill()
				_ = command.Wait()
				t.Fatalf("client did not hold input: %s", output)
			}
		case <-time.After(12 * time.Second):
			_ = command.Process.Kill()
			_ = command.Wait()
			t.Fatal("held input timed out")
		}
		var originalSession string
		if boundary == "user_switch" {
			seat, seatErr := loginSeat(context.Background(), "seat0")
			if seatErr != nil || seat.kind != "user" {
				_ = command.Process.Kill()
				_ = command.Wait()
				t.Fatal("user-switch fixture has no active user")
			}
			originalSession = seat.session
			defer exec.Command("/usr/bin/loginctl", "activate", originalSession).Run()
			if exec.Command("/usr/bin/busctl", "--system", "call", "org.gnome.DisplayManager", "/org/gnome/DisplayManager/LocalDisplayFactory", "org.gnome.DisplayManager.LocalDisplayFactory", "CreateTransientDisplay").Run() != nil {
				_ = command.Process.Kill()
				_ = command.Wait()
				t.Fatal("GDM user-switch fixture unavailable")
			}
			deadline := time.Now().Add(10 * time.Second)
			for {
				successor, successorErr := loginSeat(context.Background(), "seat0")
				if successorErr == nil && successor.session != originalSession && successor.kind == "greeter" {
					greeter := successor.session
					defer func() {
						_ = exec.Command("/usr/bin/loginctl", "activate", originalSession).Run()
						_ = exec.Command("/usr/bin/loginctl", "terminate-session", greeter).Run()
					}()
					break
				}
				if time.Now().After(deadline) {
					_ = command.Process.Kill()
					_ = command.Wait()
					t.Fatal("GDM did not publish its greeter")
				}
				time.Sleep(50 * time.Millisecond)
			}
		} else if boundary == "service_stop" {
			_, err = ManageLoginScreenService(context.Background(), LoginServiceDeploymentRequest{Operation: "stop"}, report)
			if err != nil {
				_ = command.Process.Kill()
				_ = command.Wait()
				t.Fatal("stop with held input failed")
			}
		} else {
			_ = command.Process.Kill()
		}
		_ = command.Wait()
		deadline := time.Now().Add(3 * time.Second)
		for {
			paths, _ := filepath.Glob("/sys/class/input/event*/device/name")
			remaining := false
			for _, path := range paths {
				data, _ := os.ReadFile(path)
				if string(data) == "Redeven remote keyboard\n" || string(data) == "Redeven remote pointer\n" {
					remaining = true
				}
			}
			if !remaining {
				break
			}
			if time.Now().After(deadline) {
				t.Fatal("lifetime boundary retained virtual input")
			}
			time.Sleep(20 * time.Millisecond)
		}
		t.Logf("%s released held input and destroyed the virtual devices", boundary)
		if boundary == "service_stop" {
			if _, err = ManageLoginScreenService(context.Background(), LoginServiceDeploymentRequest{Operation: "start"}, report); err != nil {
				t.Fatal("restart after held input failed")
			}
		}
		client()
		if originalSession != "" {
			if exec.Command("/usr/bin/loginctl", "activate", originalSession).Run() != nil {
				t.Fatal("original desktop reactivation failed")
			}
			deadline := time.Now().Add(10 * time.Second)
			for {
				seat, seatErr := loginSeat(context.Background(), "seat0")
				if seatErr == nil && seat.session == originalSession {
					break
				}
				if time.Now().After(deadline) {
					t.Fatal("original desktop did not reactivate")
				}
				time.Sleep(50 * time.Millisecond)
			}
		}
	}
	for _, operation := range []string{"stop", "start"} {
		if _, err := ManageLoginScreenService(context.Background(), LoginServiceDeploymentRequest{Operation: operation}, report); err != nil {
			t.Fatalf("%s failed", operation)
		}
	}
	client()
	request.Operation = "update"
	if _, err := ManageLoginScreenService(context.Background(), request, report); err != nil {
		t.Fatal("idempotent update failed")
	}
	if _, err := ManageLoginScreenService(context.Background(), LoginServiceDeploymentRequest{Operation: "uninstall"}, report); err != nil {
		t.Fatal("uninstall failed")
	}
	for _, name := range []string{"/etc/systemd/system/redeven-desktop.service", "/usr/lib/redeven-desktop", "/run/redeven-desktop", "/etc/modules-load.d/redeven-desktop.conf"} {
		if _, err := os.Lstat(name); !errors.Is(err, os.ErrNotExist) {
			t.Fatal("uninstall retained files or socket")
		}
	}
}
