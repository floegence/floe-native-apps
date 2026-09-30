package nativeapps

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestDesktopSessionOptionsPreserveExactEnvironment(t *testing.T) {
	directory := t.TempDir()
	if err := os.Chmod(directory, 0700); err != nil {
		t.Fatal(err)
	}
	options := DesktopSessionOptions{Directory: filepath.Join(directory, "instance"), Runtime: directory,
		Instance: "session-1", Environment: []string{"LANG=C.UTF-8", "VALUE=quoted ' $ = 中文", "EMPTY="}}
	got, err := validateDesktopSessionOptions(options)
	if err != nil || got["VALUE"] != "quoted ' $ = 中文" || got["EMPTY"] != "" {
		t.Fatal("environment changed before planning", got, err)
	}
	for name, change := range map[string]func(*DesktopSessionOptions){
		"ambient environment": func(o *DesktopSessionOptions) { o.Environment = nil },
		"duplicate key":       func(o *DesktopSessionOptions) { o.Environment = []string{"VALUE=a", "VALUE=b"} },
		"missing separator":   func(o *DesktopSessionOptions) { o.Environment = []string{"VALUE"} },
		"invalid key":         func(o *DesktopSessionOptions) { o.Environment = []string{"A-B=value"} },
		"nul":                 func(o *DesktopSessionOptions) { o.Environment = []string{"VALUE=\x00"} },
		"invalid utf8":        func(o *DesktopSessionOptions) { o.Environment = []string{"VALUE=\xff"} },
		"relative document":   func(o *DesktopSessionOptions) { o.InitialDocuments = []string{"relative"} },
		"too many documents":  func(o *DesktopSessionOptions) { o.InitialDocuments = make([]string, 65) },
		"invalid instance":    func(o *DesktopSessionOptions) { o.Instance = "../other" },
		"relative directory":  func(o *DesktopSessionOptions) { o.Directory = "relative" },
		"invalid bus":         func(o *DesktopSessionOptions) { o.HostBus = "unix:path=\x00" },
	} {
		t.Run(name, func(t *testing.T) {
			value := options
			change(&value)
			if _, err := validateDesktopSessionOptions(value); !errors.Is(err, ErrInvalid) {
				t.Fatal("invalid launch resources accepted", err)
			}
		})
	}
	link := filepath.Join(directory, "runtime-link")
	if err := os.Symlink(directory, link); err != nil {
		t.Fatal(err)
	}
	options.Runtime = link
	if _, err := validateDesktopSessionOptions(options); !errors.Is(err, ErrInvalid) {
		t.Fatal("runtime symlink accepted", err)
	}
	options.Runtime = directory
	if err := os.Chmod(directory, 0755); err != nil {
		t.Fatal(err)
	}
	if _, err := validateDesktopSessionOptions(options); !errors.Is(err, ErrInvalid) {
		t.Fatal("shared runtime accepted", err)
	}
}

func TestDesktopSessionRejectsUnknownComponentWithoutChangingExistingDirectory(t *testing.T) {
	pkg, err := DesktopForPlatform("linux", "amd64")
	if err != nil {
		t.Fatal(err)
	}
	manager, err := New(t.TempDir(), pkg, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer manager.Close()
	directory := t.TempDir()
	marker := filepath.Join(directory, "user-data")
	if err := os.WriteFile(marker, []byte("preserve"), 0600); err != nil {
		t.Fatal(err)
	}
	plan := ApplicationLaunchPlan{snapshot: json.RawMessage(`{"backend":{"id":"wayland","component":"unknown"}}`)}
	if _, err := manager.PrepareDesktopSession(context.Background(), DesktopSessionOptions{Directory: directory, Plan: plan}); !errors.Is(err, ErrInvalid) {
		t.Fatal("uninstalled component was prepared", err)
	}
	if data, err := os.ReadFile(marker); err != nil || string(data) != "preserve" {
		t.Fatal("existing resource changed", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := prepareDesktopSession(ctx, "", "amd64", "", DesktopSessionOptions{Directory: directory}); !errors.Is(err, context.Canceled) {
		t.Fatal("cancelled preparation proceeded", err)
	}
}

func TestDesktopRetainedRecipeRequiresUpdateOnlyForNewLaunches(t *testing.T) {
	pkg, err := DesktopForPlatform("linux", "amd64")
	if err != nil {
		t.Fatal(err)
	}
	manager, err := New(t.TempDir(), pkg, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer manager.Close()
	previous := manager.installations[1]
	manager.op.Installed = previous.Digest
	if previous.ID != "alpine-3.23-desktop-14.0.2-amd64-r2" {
		t.Fatal("lost retained desktop recipe")
	}
	if _, err := manager.DesktopBackend(); !errors.Is(err, ErrUnsupported) {
		t.Fatal("mixed capture ABI", err)
	}
	plan := ApplicationLaunchPlan{snapshot: json.RawMessage(`{"backend":{"id":"wayland","component":"` + previous.Digest + `"}}`)}
	if _, err := manager.PrepareDesktopSession(t.Context(), DesktopSessionOptions{Plan: plan}); !errors.Is(err, ErrInvalid) {
		t.Fatal("prepared incompatible capture", err)
	}
	if _, ok := manager.installation(previous.Digest); !ok {
		t.Fatal("retired live component")
	}
}
