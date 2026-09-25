package nativeapps

import (
	"bytes"
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"io"
	"os/exec"
	"path/filepath"
)

//go:embed launch_plan.py
var applicationPlanner []byte

// BackendCapability describes one prepared backend. The trusted host obtains
// these values from its installed backend, never from a browser request. A
// capability describes available resources, not application readiness.
type BackendCapability struct {
	ID        string   `json:"id"`
	Component string   `json:"component"`
	Protocols []string `json:"protocols"`
	Services  []string `json:"services,omitempty"`
}

// ApplicationPlanOptions contains the already authorized host desktop entry.
// Python must provide GIO. Environment is the original host environment, or a
// Tools.Environment result whose saved overrides the planner restores.
type ApplicationPlanOptions struct {
	Python      string
	Environment []string
	DesktopFile string
	Backends    []BackendCapability
}

// LaunchUnavailable is safe for production diagnostics. It contains no child
// output, input text, environment values, or desktop file contents.
type LaunchUnavailable struct {
	Code  string `json:"code"`
	Stage string `json:"stage"`
}

func (e *LaunchUnavailable) Error() string { return e.Stage + ": " + e.Code }

// ApplicationLaunchPlan is an immutable, short-lived snapshot. It is not an
// authorization token, a persistent application record, or a backend fallback.
// Package updates require a fresh plan before executing the application.
type ApplicationLaunchPlan struct{ snapshot json.RawMessage }

// Write places this plan in the new application's private directory. Invoke the
// installed application launcher as --plan PATH RECEIPT. It revalidates before
// GIO creates a child; graphical resources must already match Backend.Component.
// Do not overwrite a running instance's plan or change its backend identity.
func (p ApplicationLaunchPlan) Write(directory string) (string, error) {
	if len(p.snapshot) == 0 {
		return "", ErrInvalid
	}
	return writeApplicationFile(directory, "application-plan.json", p.snapshot, false)
}

// ApplicationPlanDescription describes identity and required resources without
// granting readiness or input permission. Each call returns independent values.
type ApplicationPlanDescription struct {
	Version       int                `json:"version"`
	SHA256        string             `json:"sha256"`
	Backend       BackendCapability  `json:"backend"`
	Package       ApplicationPackage `json:"package"`
	Services      []string           `json:"services"`
	Desktop       string             `json:"desktop"`
	DesktopSHA256 string             `json:"desktop_sha256"`
}

type ApplicationPackage struct {
	Kind        string `json:"kind"`
	ID          string `json:"id"`
	Revision    string `json:"revision"`
	Version     string `json:"version,omitempty"`
	Confinement string `json:"confinement,omitempty"`
	Application string `json:"application,omitempty"`
	SecurityTag string `json:"security_tag,omitempty"`
	Ref         string `json:"ref,omitempty"`
	Deployment  string `json:"deployment,omitempty"`
	Format      int    `json:"format,omitempty"`
}

func (p ApplicationLaunchPlan) Description() ApplicationPlanDescription {
	var snapshot struct {
		Version     int               `json:"version"`
		SHA256      string            `json:"sha256"`
		Backend     BackendCapability `json:"backend"`
		Observation struct {
			Package  ApplicationPackage `json:"package"`
			Services []string           `json:"services"`
			Desktop  struct {
				Path   string `json:"path"`
				SHA256 string `json:"sha256"`
			} `json:"desktop"`
		} `json:"observation"`
	}
	_ = json.Unmarshal(p.snapshot, &snapshot)
	return ApplicationPlanDescription{Version: snapshot.Version, SHA256: snapshot.SHA256,
		Backend: snapshot.Backend, Package: snapshot.Observation.Package,
		Services: snapshot.Observation.Services, Desktop: snapshot.Observation.Desktop.Path,
		DesktopSHA256: snapshot.Observation.Desktop.SHA256}
}

// PlanApplication observes the desktop entry and installed package metadata
// without starting an application. Unknown protocol metadata selects the combined
// Wayland/Xwayland backend; its absence is explicit, never an Xpra retry.
func PlanApplication(ctx context.Context, options ApplicationPlanOptions) (ApplicationLaunchPlan, error) {
	if !filepath.IsAbs(options.DesktopFile) {
		return ApplicationLaunchPlan{}, ErrInvalid
	}
	return runApplicationPlanner(ctx, options, map[string]any{"operation": "prepare",
		"desktop": options.DesktopFile, "backends": options.Backends})
}

// Revalidate rereads the same desktop entry, executable and package revision.
// The supervisor must also revalidate at its execution boundary. Calling this
// method alone cannot authorize a later unchecked launch.
func (p ApplicationLaunchPlan) Revalidate(ctx context.Context, options ApplicationPlanOptions) error {
	if len(p.snapshot) == 0 {
		return &LaunchUnavailable{Code: "APPLICATION_PLAN_STALE", Stage: "revalidation"}
	}
	if options.DesktopFile != p.Description().Desktop {
		return &LaunchUnavailable{Code: "APPLICATION_PLAN_STALE", Stage: "revalidation"}
	}
	_, err := runApplicationPlanner(ctx, options, map[string]any{"operation": "revalidate",
		"plan": p.snapshot, "backends": options.Backends})
	return err
}

type plannerOutput struct{ bytes.Buffer }

func (b *plannerOutput) Write(data []byte) (int, error) {
	if b.Len()+len(data) > 2<<20 {
		return 0, errors.New("application planner output exceeds limit")
	}
	return b.Buffer.Write(data)
}

func runApplicationPlanner(ctx context.Context, options ApplicationPlanOptions, request any) (ApplicationLaunchPlan, error) {
	if !filepath.IsAbs(options.Python) || options.Environment == nil {
		return ApplicationLaunchPlan{}, ErrInvalid
	}
	data, err := json.Marshal(request)
	if err != nil || len(data) > 2<<20 {
		return ApplicationLaunchPlan{}, ErrInvalid
	}
	command := exec.CommandContext(ctx, options.Python, "-c", string(applicationPlanner))
	command.Env = options.Environment
	command.Stdin = bytes.NewReader(data)
	var output plannerOutput
	command.Stdout, command.Stderr = &output, io.Discard
	runErr := command.Run()
	if ctx.Err() != nil {
		return ApplicationLaunchPlan{}, ctx.Err()
	}
	var result struct {
		Plan  json.RawMessage    `json:"plan"`
		Error *LaunchUnavailable `json:"error"`
	}
	if json.Unmarshal(output.Bytes(), &result) == nil {
		if result.Error != nil && result.Error.Code != "" && result.Error.Stage != "" {
			return ApplicationLaunchPlan{}, result.Error
		}
		if runErr == nil && len(result.Plan) > 0 {
			plan := ApplicationLaunchPlan{snapshot: bytes.Clone(result.Plan)}
			if plan.Description().Version == 1 && len(plan.Description().SHA256) == 64 {
				return plan, nil
			}
		}
	}
	return ApplicationLaunchPlan{}, &LaunchUnavailable{Code: "APPLICATION_PROBE_FAILED", Stage: "planning"}
}
