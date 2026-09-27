package nativeapps

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"unicode/utf8"
)

// Environment isolates the planning interpreter while retaining the exact host
// resolution environment in the existing supervisor handoff.
func (t DesktopTools) Environment(base []string) []string {
	return supportToolEnvironment(base, filepath.Join(t.Root, "floe", "desktop", "bin"))
}

// DesktopBackend returns the installed combined display capability. A surviving
// Xpra selection remains Xpra until preparation explicitly activates its update.
func (m *Manager) DesktopBackend() (BackendCapability, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	item, ok := m.installation(m.op.Installed)
	if m.closed || !ok || item.Contract != desktopContract {
		return BackendCapability{}, ErrUnsupported
	}
	if _, err := m.installedDirectory(item.Digest); err != nil {
		return BackendCapability{}, err
	}
	return desktopBackend(item.Digest), nil
}

func desktopBackend(component string) BackendCapability {
	return BackendCapability{ID: "wayland", Component: component, Protocols: []string{"wayland", "x11"},
		Services: []string{"user-systemd-scope", "file-portal", "document-portal", "ibus-portal"}}
}

// PlanDesktop observes an authorized desktop entry against the verified installed
// component. Missing package/host services remain structured planning failures.
func (m *Manager) PlanDesktop(ctx context.Context, desktopFile string, environment []string) (ApplicationLaunchPlan, error) {
	backend, err := m.DesktopBackend()
	if err != nil {
		return ApplicationLaunchPlan{}, err
	}
	root, err := m.DirectoryFor(backend.Component)
	if err != nil {
		return ApplicationLaunchPlan{}, err
	}
	tools, err := ResolveDesktopTools(root, m.pkg.Architecture)
	if err != nil {
		return ApplicationLaunchPlan{}, err
	}
	return PlanApplication(ctx, ApplicationPlanOptions{Python: tools.Python, Environment: tools.Environment(environment),
		DesktopFile: desktopFile, Backends: []BackendCapability{backend}})
}

// DesktopSessionOptions is an authorized new instance, never a renderer payload.
// Directory must not exist; Runtime is an existing owner-only socket directory.
// Environment is the original host environment, not a support-tool environment.
type DesktopSessionOptions struct {
	Directory, Runtime, Instance string
	Plan                         ApplicationLaunchPlan
	Environment                  []string
	HostBus                      string
	InitialDocuments             []string
}

// PreparedDesktopSession contains immutable launch resources. Launch Executable
// with Configuration as its sole argument using a persistent process owner.
// Cancelling a viewer or Runtime attachment must never terminate this process.
type PreparedDesktopSession struct {
	Executable, Configuration, Component string
	Endpoint                             DesktopEndpoint
}

type desktopLaunchConfiguration struct {
	Version          int               `json:"version"`
	Instance         string            `json:"instance"`
	Token            string            `json:"token"`
	Directory        string            `json:"directory"`
	Runtime          string            `json:"runtime"`
	Environment      map[string]string `json:"environment"`
	Resources        map[string]string `json:"resources"`
	Plan             json.RawMessage   `json:"plan"`
	HostBus          *string           `json:"host_bus"`
	InitialDocuments []string          `json:"initial_documents"`
}

func (t DesktopTools) resources() map[string]string {
	return map[string]string{"component": t.Root, "shell": t.Shell, "capture": t.Capture, "library": t.Library,
		"xwayland": t.Xwayland, "ibus_daemon": t.IBusDaemon, "ibus_portal": t.IBusPortal, "qt_plugins": t.QtPlugins}
}

// PrepareDesktopSession binds a plan to a known installed component, generates
// authentication and writes a new immutable source/configuration snapshot. It
// neither launches applications nor rewrites surviving session resources.
func (m *Manager) PrepareDesktopSession(ctx context.Context, options DesktopSessionOptions) (PreparedDesktopSession, error) {
	description := options.Plan.Description()
	m.mu.Lock()
	item, ok := m.installation(description.Backend.Component)
	closed := m.closed
	m.mu.Unlock()
	if closed || !ok || item.Contract != desktopContract || description.Backend.ID != "wayland" {
		return PreparedDesktopSession{}, ErrInvalid
	}
	root, err := m.DirectoryFor(item.Digest)
	if err != nil {
		return PreparedDesktopSession{}, err
	}
	return prepareDesktopSession(ctx, root, item.Architecture, item.Digest, options)
}

func prepareDesktopSession(ctx context.Context, root, architecture, component string, options DesktopSessionOptions) (PreparedDesktopSession, error) {
	if err := ctx.Err(); err != nil {
		return PreparedDesktopSession{}, err
	}
	environment, err := validateDesktopSessionOptions(options)
	if err != nil {
		return PreparedDesktopSession{}, err
	}
	tools, err := ResolveDesktopTools(root, architecture)
	if err != nil {
		return PreparedDesktopSession{}, err
	}
	if options.Plan.Description().Backend.Component != component {
		return PreparedDesktopSession{}, ErrInvalid
	}
	if err := options.Plan.Revalidate(ctx, ApplicationPlanOptions{Python: tools.Python, Environment: tools.Environment(options.Environment),
		DesktopFile: options.Plan.Description().Desktop, Backends: []BackendCapability{desktopBackend(component)}}); err != nil {
		return PreparedDesktopSession{}, err
	}
	if err := os.Mkdir(options.Directory, 0700); err != nil {
		return PreparedDesktopSession{}, err
	}
	created, err := os.Lstat(options.Directory)
	if err != nil {
		return PreparedDesktopSession{}, err
	}
	complete := false
	defer func() {
		if !complete {
			if current, err := os.Lstat(options.Directory); err == nil && os.SameFile(created, current) {
				_ = os.RemoveAll(options.Directory)
			}
		}
	}()
	entry, err := WriteDesktopLauncher(filepath.Join(options.Directory, "helper"), root, architecture)
	if err != nil {
		return PreparedDesktopSession{}, err
	}
	var token [32]byte
	if _, err := rand.Read(token[:]); err != nil {
		return PreparedDesktopSession{}, err
	}
	value := desktopLaunchConfiguration{Version: 1, Instance: options.Instance, Token: hex.EncodeToString(token[:]), Directory: options.Directory,
		Runtime: options.Runtime, Environment: environment, Resources: tools.resources(), Plan: options.Plan.snapshot, InitialDocuments: append([]string{}, options.InitialDocuments...)}
	if options.HostBus != "" {
		value.HostBus = &options.HostBus
	}
	data, err := json.Marshal(value)
	if err != nil || len(data) > 1<<20 {
		return PreparedDesktopSession{}, ErrInvalid
	}
	config := filepath.Join(options.Directory, "desktop-launch.json")
	if err := os.WriteFile(config, data, 0600); err != nil {
		return PreparedDesktopSession{}, err
	}
	if err := ctx.Err(); err != nil {
		return PreparedDesktopSession{}, err
	}
	complete = true
	return PreparedDesktopSession{Executable: entry, Configuration: config, Component: component,
		Endpoint: DesktopEndpoint{SocketPath: filepath.Join(options.Runtime, "control.sock"), Instance: options.Instance, Token: value.Token}}, nil
}

var desktopInstancePattern = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,128}$`)
var desktopEnvironmentPattern = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

func validateDesktopSessionOptions(options DesktopSessionOptions) (map[string]string, error) {
	absolute := func(value string) bool {
		return filepath.IsAbs(value) && utf8.ValidString(value) && !strings.ContainsRune(value, 0)
	}
	if !absolute(options.Directory) || !absolute(options.Runtime) ||
		!desktopInstancePattern.MatchString(options.Instance) || options.Environment == nil || len(options.InitialDocuments) > 64 ||
		len(options.Environment) > 512 || !utf8.ValidString(options.HostBus) || strings.ContainsRune(options.HostBus, 0) {
		return nil, ErrInvalid
	}
	if err := validateDesktopRuntime(options.Runtime); err != nil {
		return nil, err
	}
	environment := map[string]string{}
	for _, item := range options.Environment {
		key, value, ok := strings.Cut(item, "=")
		if !ok || !desktopEnvironmentPattern.MatchString(key) || !utf8.ValidString(value) || strings.ContainsRune(value, 0) {
			return nil, ErrInvalid
		}
		if _, duplicate := environment[key]; duplicate {
			return nil, ErrInvalid
		}
		environment[key] = value
	}
	for _, document := range options.InitialDocuments {
		if !absolute(document) {
			return nil, ErrInvalid
		}
	}
	return environment, nil
}
