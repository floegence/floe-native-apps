package nativeapps

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
)

func TestApplicationPlanDescriptionCannotMutatePlan(t *testing.T) {
	plan := ApplicationLaunchPlan{snapshot: json.RawMessage(`{"version":1,"sha256":"fixture",
		"backend":{"id":"wayland","component":"private","protocols":["wayland","x11"]},
		"observation":{"desktop":{"path":"/fixture.desktop","sha256":"source"},
		"package":{"kind":"snap","id":"fixture","revision":"42"},"services":["user-systemd-scope"]}}`)}
	description := plan.Description()
	description.Backend.Protocols[0] = "changed"
	description.Services[0] = "changed"
	description.Package.Revision = "43"
	actual := plan.Description()
	if actual.Backend.Protocols[0] != "wayland" || actual.Services[0] != "user-systemd-scope" || actual.Package.Revision != "42" {
		t.Fatalf("public description modified immutable plan: %+v", actual)
	}
	path, err := plan.Write(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path)
	if err != nil || string(data) != string(plan.snapshot) {
		t.Fatalf("private plan changed: %s, %v", data, err)
	}
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatalf("private plan permissions: %v, %v", info, err)
	}
	if _, err := plan.Write(filepath.Dir(path)); err == nil {
		t.Fatal("plan overwrote an existing instance snapshot")
	}
	unchanged, err := os.ReadFile(path)
	if err != nil || string(unchanged) != string(data) {
		t.Fatalf("existing plan changed: %v", err)
	}
}

func TestApplicationPlanRejectsEmptyAndDifferentTarget(t *testing.T) {
	var plan ApplicationLaunchPlan
	if _, err := plan.Write(t.TempDir()); !errors.Is(err, ErrInvalid) {
		t.Fatalf("empty plan was written: %v", err)
	}
	for _, value := range []ApplicationLaunchPlan{plan, {snapshot: json.RawMessage(`{"observation":{"desktop":{"path":"/first.desktop"}}}`)}} {
		err := value.Revalidate(context.Background(), ApplicationPlanOptions{DesktopFile: "/other.desktop"})
		var unavailable *LaunchUnavailable
		if !errors.As(err, &unavailable) || unavailable.Code != "APPLICATION_PLAN_STALE" {
			t.Fatalf("invalid plan reached process probe: %v", err)
		}
	}
}

func TestApplicationPlannerRequiresExplicitHostEnvironment(t *testing.T) {
	_, err := PlanApplication(context.Background(), ApplicationPlanOptions{
		Python: filepath.Join(t.TempDir(), "python"), DesktopFile: "/fixture.desktop",
	})
	if !errors.Is(err, ErrInvalid) {
		t.Fatalf("planner inherited ambient support environment: %v", err)
	}
}

func TestApplicationPlannerBoundsProtocolOutput(t *testing.T) {
	var output plannerOutput
	if _, err := output.Write(make([]byte, 2<<20)); err != nil {
		t.Fatal(err)
	}
	if _, err := output.Write([]byte("x")); err == nil || output.Len() != 2<<20 {
		t.Fatalf("planner output exceeded bound: %d, %v", output.Len(), err)
	}
}

func TestNativeApplicationPlan(t *testing.T) {
	python := os.Getenv("FLOE_TEST_PLAN_PYTHON")
	if python == "" {
		t.Skip("explicit native GIO launch-plan qualification")
	}
	if runtime.GOOS != "linux" {
		t.Fatal("native Linux qualification required")
	}
	root := t.TempDir()
	desktop := filepath.Join(root, "fixture.desktop")
	source := "[Desktop Entry]\nType=Application\nName=Floe plan fixture\nExec=/bin/sh -c \"exit 46\"\n"
	if err := os.WriteFile(desktop, []byte(source), 0600); err != nil {
		t.Fatal(err)
	}
	options := ApplicationPlanOptions{Python: python, Environment: os.Environ(), DesktopFile: desktop,
		Backends: []BackendCapability{{ID: "wayland", Component: "identity-fixture-only", Protocols: []string{"wayland", "x11"}}}}
	plan, err := PlanApplication(context.Background(), options)
	if err != nil {
		t.Fatal(err)
	}
	if err = plan.Revalidate(context.Background(), options); err != nil {
		t.Fatal(err)
	}
	launcher, err := WriteApplicationLauncher(root)
	if err != nil {
		t.Fatal(err)
	}
	path, err := plan.Write(root)
	if err != nil {
		t.Fatal(err)
	}
	receipt := filepath.Join(root, "receipt.json")
	run := func(expected int) map[string]any {
		t.Helper()
		command := exec.Command(python, launcher, "--plan", path, receipt)
		command.Env = options.Environment
		if err := command.Run(); err == nil || command.ProcessState.ExitCode() != expected {
			t.Fatalf("supervisor exit: %v, want %d", err, expected)
		}
		data, err := os.ReadFile(receipt)
		if err != nil {
			t.Fatal(err)
		}
		var status map[string]any
		if err := json.Unmarshal(data, &status); err != nil {
			t.Fatal(err)
		}
		return status
	}
	if status := run(46); status["exit_code"] != float64(46) {
		t.Fatalf("launcher lost real exit status: %v", status)
	}
	if err := os.WriteFile(desktop, []byte(source+"Comment=Changed after planning\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if status := run(1); status["error_code"] != "APPLICATION_PLAN_STALE" {
		t.Fatalf("launcher did not reject stale plan: %v", status)
	}
	t.Logf("native %s: immutable Go plan, revalidation, private files, GIO exit 46 and stale rejection passed", runtime.GOARCH)
}
