# Contributing

Floe Native Apps owns native component preparation. Keep application inventory,
product authorization, viewers, and application sessions in the consuming host.
Read [AGENTS.md](AGENTS.md) for the implementation and repository boundaries.

## Development

Use Go **1.27.1**, Python 3, Node.js 20 or later, Git, and a POSIX shell. The Go version in `go.mod`
is authoritative and must remain aligned with Redeven. CI reads that file;
workflow YAML must not carry an independent Go version.

Enable the repository's exact-tip source gate once per clone:

```sh
git config core.hooksPath .githooks
```

Create a feature branch in a dedicated worktree. Before committing, inspect
`git var GIT_AUTHOR_IDENT` and `git var GIT_COMMITTER_IDENT`. Use your actual
verified contributor identity; do not invent a name or GitHub noreply address.
Repository-local identity settings are preferable to an unintended global
fallback. `.mailmap` may correct historical display attribution without changing
published commit IDs or module checksums.

Run `./scripts/check.sh`. It is the same source-only gate used on Linux and
macOS in GitHub Actions. `go mod tidy -diff` must produce no changes, and
`go mod verify` must pass. The `tool` directives pin `actionlint` and
`govulncheck`; keep their real checksums in `go.sum`. Do not add runtime
libraries solely to create a checksum file.

Add a focused regression test for a behavior change. Tests must preserve the
fail-closed extraction, ownership, cancellation, and activation boundaries.
Keep network and graphical tests out of ordinary source CI. Do not weaken
verification or add a privileged fallback to make a test pass.

Use English and Conventional Commits, such as
`fix(runtime): reject conflicting upload chunks`. Keep changes coherent,
explain observable behavior, and include validation and limitations in the
change description. Vulnerabilities belong in private reports, not public issues.

## Native qualification

On a native Linux host of each supported architecture:

```sh
CGO_ENABLED=0 go build -trimpath -o /tmp/floe-native-check ./cmd/floe-native-apps
/tmp/floe-native-check -state /absolute/private/state
NATIVE_CHECK=/tmp/floe-native-check \
NATIVE_ROOT=/absolute/private/state/packages/PRINTED_PACKAGE_DIGEST \
  ./scripts/qualify.sh
```

The clean distribution fixtures need Docker and read access to the disposable
component tree. Use an existing unprivileged account inside each fixture. Do
not change permissions on a real user's state to support qualification. The
production path must never require Docker. A successful check proves a live
private bus, GIO launch, a window, decoded pixels, and delivered input.

`Release qualification` performs fresh downloads and installation on native
amd64 and arm64 runners. It records install and distribution logs as workflow
artifacts. Changes to native binaries, wrappers, environment isolation,
extraction, or self-check behavior require this evidence; cross-compilation alone
is insufficient. Fixture coverage is not a certification of every host kernel,
security policy, GPU driver, or third-party application.

## Catalog maintenance

The catalog is reviewed source, not runtime configuration. Acquire original
publisher archives, preserve exact URLs and licensing/source metadata, and
verify the full architecture-specific closure. `scripts/catalog.py` assembles
metadata from an acquired package directory; it does not authorize an update.
Review generated diffs, test malicious archives, and qualify both architectures
before releasing a new catalog. No downloaded package manager hooks are run.

The Xvfb relocation accepts one reviewed `/usr/bin` literal and replaces it with
a private relative path without changing the ELF layout. Any change in upstream
binary shape must fail until explicitly reviewed and qualified. Do not generalize
this into arbitrary executable patching.

Catalog revision `r2` also prepares the pinned Xpra 6.2.2 Python WebSocket
decoder with a bounded short-frame correction. The publisher's native mask
extension does not bound its alignment prefix by the payload length. Preparation
requires the reviewed source block, retains the publisher's license header and
all other decoder behavior, and invalidates only that module's cached bytecode.
Payloads shorter than four bytes use bounded Python decoding; graphics packets
keep the native path. No modified third-party archive is redistributed. Remove
this preparation patch when a corrected publisher catalog passes the unchanged
short-frame and disconnect/reconnect checks. Both architectures must qualify.

The graphical check covers Unix sockets and three WebSocket attachments. Each
attachment must paint, deliver fresh input, and preserve the fixture's process
identity; each browser-style detach must leave its window available for the next
viewer. The standalone decoder check also covers short, aligned, and extended
frames against the installed native package before activation.

The Go vulnerability scanner checks Go code and the standard library, not the
catalog's native APK packages. Catalog updates also require review of publisher
security advisories and dependency versions. CodeQL does not replace native
component maintenance or license review.

## Release criteria

1. Finish the feature in its worktree, rebase onto the current remote main, and
   inspect the final diff. Run the source gate and relevant regression checks.
2. Fast-forward main and publish its full tip. Keep feature branches private
   unless a collaborator explicitly requests a pull request. Verify main's
   `Source checks` result and the scheduled/manual security analyses.
3. Create a new immutable semantic-version tag on that main commit. Tags trigger
   `Release qualification`, including source checks, Go vulnerability checks,
   fresh native installations, and both architecture matrices. Publish the
   GitHub release only after its `Release gate` succeeds. Do not repoint or
   delete published tags or replace their module bytes.
4. Release notes must identify behavior changes, toolchain, qualification
   evidence, and relevant limitations. Keep third-party native archives at their
   original publishers; do not attach an aggregate binary stack without a
   separate license-compliant distribution process.
5. Verify the tagged module through `proxy.golang.org` and the checksum database
   before consumers upgrade. Consumers must test the published module with
   `GOWORK=off`; sibling checkout wiring is not release evidence.
6. Remove only the task-owned merged worktree and branch.

Repository protection requires the `Source checks` context, linear history,
resolved review conversations, and prohibits force pushes and branch deletion.
Administrators retain the documented direct-main integration path through the
local pre-push source gate and must verify its GitHub source result. CodeQL runs daily and manually, outside ordinary
push/PR CI. Release qualification is the heavier release lane.

Application lifetime qualification also covers windowless processes, intermediate
launcher exit, double-forked descendants, the restored host environment and failed
launch receipts, plus explicit supervisor termination without surviving children.
Linux launch requires kernel pidfds (Linux 5.3 or later); an unavailable primitive
fails before launch. Explicit supervisor SIGTERM kills only the launcher's owned
descendants using pidfds and verified parent relationships. Consumers must reserve
it for explicit force-quit intent, never viewer detach or cancelled save dialogs.
Child subreaping is enabled before GIO spawns the application;
there is one wait owner. Applications that delegate to unrelated pre-existing
processes or an external system service are outside this child-tree contract.
A host must not infer process exit from a missing window, or revoke sharing merely
by deleting a route while its upgraded connections remain open.
The launcher receipt preserves direct-child exit codes, including Snap's exit 46,
and reports whether explicit supervisor termination was requested. Spawn failures
carry a stable error code and phase. Process results do not establish graphical
readiness; the backend must distinguish startup failure from an established
application ending using its own admitted window lifecycle. Intermediate wrapper
exit still cannot end the supervisor while owned descendants remain.

`PlanApplication` resolves an authorized desktop entry with GIO and binds its
source bytes, executable identity, package revision and prepared backend. Package
identity comes from snapd, Flatpak deployment metadata, dpkg/RPM or the AppImage
header and content digest; it never determines the graphical protocol by app name.
Unknown protocol metadata requires the prepared combined Wayland/Xwayland
capability. Missing capabilities and required host services fail before execution,
without a backend retry. A capability is not proof that an application has a window
or accepts text. Read-only planning probes must not start the selected application.

Plans are private, immutable Go values. Description results are copies, and `Write`
places the snapshot with mode 0600 in a new instance directory. The installed GIO
supervisor accepts `--plan PATH RECEIPT` and revalidates immediately before creating
children. A changed desktop entry, executable, package revision, resolution
environment, or unsupported plan produces `APPLICATION_PLAN_STALE`; the consumer
must acquire a fresh plan instead of rewriting an existing instance. Session
display/bus assignments do not change host package resolution. Consumers must
verify the prepared component identity before assigning graphical resources.
The existing positional desktop-file invocation remains the existing Xpra launch
contract; it does not select or retry a new backend.

The native lifetime self-check also asserts actual GIO field-code expansion and
that a stale plan never executes its task-owned child. The fixture capability is
explicitly identity-only; it is not evidence for Wayland or sandbox support.

For a verified Snap plan, the application supervisor owns the private systemd
scope adapter. The prepared helper supplies `FLOE_NATIVE_HOST_BUS` separately
from the application's private session bus; this handoff is removed before GIO
executes the application. The two bus identities must differ and the real scope
manager must belong to the host user. Only a registered direct launcher may
request its own package-named scope with one PID and no extra units/properties.
Requests and completion signals use the real systemd peer's unique bus identity.
No general desktop-bus proxy, service creation, or process-management API exists.

The sole process wait boundary observes child exit without reaping a PID used by
an in-flight scope request. A pidfd alone does not prevent numeric PID reuse.
Actual systemd completion, a definitive systemd error, or that exact peer's exit
releases the lease. Local timeouts and bus-generated `NoReply` errors retain it;
they neither retry the operation nor claim it was cancelled. Native lifetime
qualification checks this kernel boundary as well as the existing adopted-child,
windowless-process, exit-status and explicit-termination behavior. The standalone
scope fixture now uses this production supervisor; it has no independent proxy.
These source and scope tests do not constitute a complete graphical-backend or
package-format support claim.

## Native helper attachment

`DialDesktop` attaches to an existing Linux helper through a private, versioned
Unix socket. A trusted application instance supplies its endpoint, identity and
token. The socket must be owned by the host user, mode 0600, in a mode 0700
directory; kernel peer credentials are checked before authentication. A successful
attach returns the native state and an immutable connection generation. Invalid
authentication must not displace the current viewer.

`DesktopConnection` has one reader and serializes concurrent writers. `Send`
returns a request ID, not an application receipt; `Read` returns native events and
correlated replies. Metadata and PNG payloads are bounded and read as one complete
event. The consumer must decode and paint a frame before sending `frame_ack`.
Input always names its original connection, window and geometry generation. The
helper remains the sole owner of admission and input ordering. There is no client
queue, automatic acknowledgement, reconnect loop or replay. Cancellation during
I/O closes the partial stream. `Close` detaches sharing and never terminates the
helper or application. Waiting, unavailable capture and connection loss are not
application-exit evidence.

`TestDesktopClient*` exercises fragmented packets, malformed framing, cancellation,
concurrent writes and the real Python control/attachment boundary on native Linux,
including takeover and late old-owner cleanup. Its synthetic native callbacks are
wire qualification only. This API does not prepare a compositor, launch an
application, or certify the unpublished combined graphical backend. Production
component preparation and the package/desktop application matrix remain required
before that backend can be published as a supported capability.

The internal `DesktopHelper` composes the native channel/window registry, bounded
capture, decoded-frame gate, ordered input and authenticated endpoint on one event
loop. The persistent launch owner supplies its prepared sockets. It starts sharing only after
the native protocol is identified and owns cleanup if endpoint creation fails.
Viewer detach leaves the registry and capture process intact; native channel loss
reports unavailable state without inventing application exit. Full helper disposal
closes its channels, while process lifetime remains with the launch supervisor.
Authenticated mixed-window probes use this same assembly rather than a second test
implementation. Complete package admission and installed-component activation remain separate release prerequisites.

The helper also owns one `NativeContexts` registry and its toolkit, IBus and XIM
services. It borrows the private bus and supervisor process authority, and takes
ownership of the native X11 resource connection. Viewer detach preserves these
services. Disposal revokes pending input before releasing services and native
resources; it neither closes the borrowed process tree nor terminates applications.
An initialized context owner cannot be recreated within the same native lifetime,
so old registration and marker callbacks cannot enter a new owner. Partial D-Bus
registration releases only the name and objects acquired by that attempt. Engine
activation remains asynchronous on the sole helper event loop.

The internal `DesktopSession` owns the private bus, combined compositor, capture,
input services and plan-required official file/settings portals before executing
the application supervisor. Native plans do not start document or file portals
and therefore need no FUSE or desktop portal session. The same immutable plan is
verified before preparation and again by the sole supervisor at execution. Its one preparation deadline ends before application execution; a
first window may appear arbitrarily later. The private socket runtime and the
instance resource directory are distinct: runtime mounts may prohibit execution,
so prepared Xwayland launchers live with the instance resources. The compositor returns its reserved
Xwayland display on the native control channel. Log text is never a startup API.
Service names are accepted only after the real bus credentials match the launched
child. Capture admission similarly binds the native child before enabling it.

Viewer detach affects only sharing. Service loss revokes input and reports an
unavailable graphical session while preserving the application and remaining
services. Session disposal rejects a still-running application supervisor. The
supervisor's bounded private receipt supplies authoritative launcher errors and
exit status; a missing or inconsistent receipt is a failure. Nonzero launcher
exit before any native window is a startup failure, while a running process with
no windows continues waiting. Component activation and package-specific resource
realization remain required before declaring the combined backend supported.

`WriteDesktopLauncher(directory, component, architecture)` installs the complete
first-party Python import closure in a new private source directory. Existing directories are rejected: updates
cannot rewrite the helper supporting a running application. Its returned entrypoint
executes the verified component's private Python through its musl loader, with
private GI/GIO resources and no inherited Python/plugin/library injection paths.
It accepts one owner-only, bounded version-1 configuration file. The configuration
binds the immutable launch plan, original application environment, private runtime,
instance/token and caller-verified compositor, capture, input-service and Qt adapter
resources. Unknown fields/versions, symlinks, public files and duplicate JSON keys
are rejected. Component resolution and package adaptation belong to the upstream
installation/launch boundary, never renderer-supplied configuration.

The application supervisor uses the same component Python and private GIO launcher.
The existing scoped environment handoff restores the application's original
interpreter, library and plugin environment before execution; only the admitted
private graphics, bus and input addresses remain. Generated executable wrappers
live in the instance resource directory, independently of a noexec socket runtime.
The portable component closure must include Python Xlib and libxcb-imdkit as well
as GI/GIO; host-installed modules must not hide a missing distribution dependency.

The entrypoint revalidates the plan before preparing resources and delegates all
processes to `DesktopSession`. Its bounded `desktop-status.json` is an atomic
receipt of that owner's native transitions/process identities, not a second
lifecycle manager. It records prepared graphics digests and distinct content-free
portal observations, never launch credentials, environment values or document
text. Explicit preparation/launcher failures return a failed helper exit while
retaining the authoritative application exit code in the receipt. Runtime-owned
stdout/stderr or stdin lifetime is not a sharing or application lifetime signal.
The helper must be launched independently of a viewer/Runtime cancellation context.

This source installation API does not activate third-party components or establish
a platform support claim. `TestNativeDesktopLauncherInstallation` explicitly
installs a retained snapshot for native qualification. The session fixture's
`FLOE_PROBE_DESKTOP_LAUNCHER` points to that installed entrypoint; it no longer
assembles a separate fixture-only session or patches production callbacks.

`qualification/desktop_compatibility/session_probe.py` runs this owner as an
independent process. Its viewer knows only authenticated IPC, decodes actual PNG
frames and checks toolkit document bytes. Native cases include ordered Unicode
and Enter in GTK4 and pure Wayland Chromium, detach/reconnect and modifier
release, a 42-second first window,
launcher exit 46, support failure before execution and capture/private-bus loss
without application termination. A restrictive task-only runtime mount verifies
that no generated executable lives on a noexec runtime. Cleanup must finish even
when the private bus is already closed; qualification checks every recorded child
and removes its own mount normally. The fixture may explicitly terminate only its
recorded test supervisor for failure cleanup; this is never viewer behavior.

The persistent Snap probe uses the same session owner and authenticated viewer
endpoint. The upstream package-resource owner generates all private graphics and
bus addresses after plan revalidation. Snap's standard owned package runtime
hosts unique Wayland and X11 authorization files; the control endpoint stays in
a separate instance-private runtime. Native and Flatpak graphics use that private
runtime directly. Inherited desktop display, authority, Wayland FD and bus addresses
are not reused. After application and service exit, authorization cleanup validates
the actual post-xauth file identity and preserves replaced files.
The launch plan requires an explicit, distinct host bus for the existing scoped
systemd adapter. No ambient host-bus variable is accepted as authorization. Real
strict Firefox scope, Unicode/Enter stress, detach/reconnect, remote official save
and byte-for-byte file receipts now exercise this owner.

Flatpak plans prepare the verified IBus portal before activating the input owner.
The session binds the official file portal's launched process before it queries
its document dependency, then independently validates bus-name readiness. The
restricted document facade binds the actual application supervisor and only its
explicit initial files; it never exposes the host desktop bus. Qt registration
uses the package's existing allowed bus namespace. The helper copies the verified
Qt5/Qt6 modules into one unique, owner-only directory inside the package's normal
`~/.var/app/<id>` root. It does not alter application profiles or sandbox permissions.
After original-plan revalidation, the sole GIO supervisor adds the official Flatpak
`--env=QT_PLUGIN_PATH` option while retaining every original Exec argument and
field code verbatim; runtime metadata otherwise overrides an inherited plugin path.
The helper removes only its own module directory after application exit, never
on viewer detach. Native applications receive the same adapters inside their
instance resource directory. Strict Snap uses standard input interfaces without
assuming it can load a host module; a focused unsupported context remains an error. Host document-service loss
reports unavailable state, while disposal preserves host grants. GTK and Qt
persistent-session fixtures deny broad host/home filesystem access, verify exact
remote Save As bytes, and remove only their own two document grants. The installed
component/public launch API and complete platform matrix remain prerequisites.

### Unpublished combined-display qualification

Copy native fixtures with
`python3 qualification/desktop_compatibility/snapshot_sources.py /absolute/new-snapshot.tar`.
The archive records the Git base, dirty state and every source digest. Extract it
only into the task-owned fixture root, while its probes are stopped. Each native
probe verifies the entire snapshot before creating resources. Do not overlay an
individual script and continue claiming the previous source snapshot.

The Chromium fixture uses actual browser input receipts. Its final captured frame
must contain the page's marker derived from the already verified document values.
PNG decoding and pixel checks precede frame acknowledgement, so geometry changes
during inspection cannot authorize input using a retired frame. These are native
application receipts, not evidence for a product viewer or a real client IME.

The same unpublished helper transports the native clipboard as bounded UTF-8
selection data, independently of confirmed-text input. Publication enters the
existing input scheduler so subsequent Paste keys cannot precede selection
ownership. Publication acknowledges ownership only: a toolkit's asynchronous
Paste can still finish after a following key. Actual widget receipts, not the
publication reply, establish pasted document bytes. The compositor uses standard
Wayland data sources and the existing Xwayland selection bridge; it never turns
clipboard data into a text-input transaction. Reads and writes are nonblocking,
bounded to 16,000 bytes with transfer timeouts, and keep only the current revision.
Unavailable, oversized or invalid UTF-8 offers produce an explicit clipboard
error. Viewer-originated publications are not echoed as application Copy events.
Only a painted target may publish or receive a snapshot; detach/target retirement
discards in-flight snapshots while retaining the application's native selection.

`session_probe.py` modes `clipboard`, `clipboard-x11` and `clipboard-chromium`
exercise GTK3/GTK4/Qt5/Qt6 Wayland/Xwayland and pure Wayland Chromium Copy/Cut/Paste,
oversized source rejection, 11,400-byte Unicode selection transfer, actual
document bytes, decoded final frames and detach/reconnect. The GTK fixture samples
the empty second editor because the long pasted document occupies the original
sample pixel. Select a toolkit with `FLOE_PROBE_TOOLKIT`; Qt records the exact modules in
`FLOE_PROBE_QT_PLUGINS`. Native arm64 results do not qualify amd64,
product clipboard permission handling or the complete distribution.

The `chromium-v3` context probe isolates native text-input-v3 state progression.
An empty `done` acknowledges each client commit; it does not acknowledge text
consumption. Chromium otherwise withholds its next state update. Actual document
and frame receipts show that repeated long tails can suppress surrounding-state
updates even after new input, so those updates cannot complete ordered input.
Each Wayland string also has a bounded native message size. The native probe
fragments valid Unicode at UTF-8 boundaries and investigates xdg-shell ping/pong
as a callback-dispatch boundary around text, Enter and field changes. A pong
does not acknowledge renderer or document consumption. The actual document and
its painted marker remain the acceptance authority, including the optional
`FLOE_PROBE_SLOW_RENDERER=1` fixture that stalls each pointer handler for 200 ms.
Protocol waits wake on records, without polling delays between operations. The
fixture keeps one document receipt in flight and coalesces obsolete snapshots;
its final slow-renderer wait allows the intentionally queued application work
to finish without adding delays to input delivery. This probe is explicitly not
the authenticated confirmed-text adapter or a release claim.

`Dockerfile.desktop-native` and `build_portable_ibus.sh` produce native musl IBus
daemon, portal and library candidates from the pinned original source and reviewed
context-origin patch. The disposable builder retains signed APKs and records its
immutable image identity, package closure, source/build hashes, ELF dependencies,
artifact hashes and original license hash. Applications retain their own host or
sandbox libraries. The candidates run through the private component loader; no
host library path or input module installation is involved. A builder image alone
does not establish application compatibility. Native amd64/arm64 GTK and
Chromium/Xwayland tests, strict Snap Firefox and Flatpak GTK/Qt save receipts are
separate acceptance evidence. Pure Wayland Chromium has no qualified confirmed-text
adapter in the published distribution yet. The development helper admits one
text-input-v3 resource for the actual focused surface and live application peer.
Its existing input scheduler brackets native commit dispatch with callback
barriers. The compositor alone selects and validates the editable context when
it sends the text, so stale helper copies of enable/disable state cannot reject
ordered focus changes or authorize another resource. Revoked connection, window,
surface and resource identities cannot receive late text or advance a new
transaction. Native rejection is final; no alternate adapter or replay follows.
Only actual application receipts establish document content and order.
These artifacts are not an activated catalog or a published support
claim; prepared helper resources, distribution and the full matrix remain required.

`DesktopServices` owns support-process resource preparation. Its input component
must already be verified by the distribution owner, and its instance directory
must be private. MIME, schema, image-loader and input-module caches are derived in
one new instance-owned directory. Generator errors and timeouts remove only that
incomplete directory; existing instance resources remain untouched and preparation
can be retried. Native generators receive the same isolated support environment
as the services. Resource paths must stay inside the component, including resolved
symlinks. XML paths are encoded as data. This implementation replaces the old
qualification-only resource assembler; it does not itself activate a component,
start services, or alter application environments.

`DesktopGraphics` prepares one combined display from caller-verified original
component bytes and explicitly selected derived compositor artifacts. All native
commands receive an isolated environment; application environment values remain
unchanged. Xwayland relocation, compiler wrapper, configuration and authorization
belong to one new private directory. Reusing an instance or an existing authority
is rejected. Failed preparation removes only resources created by that attempt;
unknown binary layouts and missing derived libraries never fall back to the host.
X11 authorization is a one-shot prerequisite before application launch. The
original component is immutable, including when it is already installed. The
shared preparer replaces the fixture's graphics assembler; artifact activation,
process orchestration and complete support claims still require qualification.

`DesktopPortals` owns the official portal command set, backend descriptions and
private configuration beneath `DesktopServices`. The private bus admits only the
reviewed file/settings, registry and request interfaces; unrelated portal calls
remain denied even if an official implementation publishes them. Backend metadata
and `portals.conf` share the same explicit directory, with no default fallback.
The service environment cannot inherit host theme, loader or input-module paths.
Flatpak identity lookup links to its real private runtime records without copying
them. Failed preparation removes only newly created resources and never removes
populated or pre-existing Flatpak records. Starting commands and confirming actual
service owners remain launch responsibilities; this source preparer is not a
completed durable helper or installed capability.

`Dockerfile.qt-native` selects an explicitly pinned native `TARGET_ARCH` and
`QT_MAJOR`, then verifies Debian's signed 20250224 snapshot. Qt 5 uses the 5.15.2 /
glibc 2.31 baseline; Qt 6 uses 6.4.2 / glibc 2.36. `build_qt_native.sh` records the
immutable builder identity, signed archive closure, source hashes, ELF requirements
and module digest. It installs no host or sandbox libraries. The qualification-only
`prepare_qt_baseline.py` exports an actual widget application and its exact baseline
library closure through a private loader. Its document and runtime receipts prove
the library versions actually executing; they are not inferred from linker flags.
The same adapter is also tested with the normal distribution and Flatpak runtimes.
These candidates remain outside the activated catalog until full qualification.

## Component compatibility qualification

An SDK release must preserve supported installed recipe identities independently
of its recommended recipe. Test v1 record adoption, v2 restart, failed and
cancelled updates with a usable installation, atomic activation, partial relay
coverage and zero-network cache-only updates. Do not change existing package
digests merely to add SDK metadata. Compatibility metadata is a reviewed
contract, not a directory-name heuristic. Historical r1 compatibility retains
its documented decoder limitation; only the updated r2 stack passes the current
short-frame qualification.

`FLOE_TEST_LEGACY_STATE=/absolute/disposable/r1-state go test -run
TestNativeLegacyComponentUpdate -v` exercises a real r1-to-r2 local update and
rejects every network request. Supply only a disposable state with original
verified archive cache entries. The release workflow runs this on both native
architectures using the published v0.2.1 installer to construct the old state.

## Client input qualification

### Display density

Prepared HTML v20/v21 clients expose `set_display_density("logical" | "native")`.
Consumers select a policy after connection startup; `false` means native density
is unavailable or the connection was disposed. Logical density remains usable on
retained backends. Selections within one JavaScript turn apply only the final
policy. Every connection starts at logical density.
Native density uses integral ceil DPR, bounded to 1 through 4 and to the server's
advertised maximum desktop dimensions. Resizing or moving between display densities
recomputes the backing resolution without reconnecting. The private display's DPI
and GTK scale change together; logical window geometry, dialog headers, cursor
hotspots and pointer targets remain stable. The version 2 display contract updates desktop dimensions, legacy screen sizes,
monitor/workarea, DPI and toolkit scale as one authenticated configuration before
native resize. A retained version 1 backend supports logical density only.
Applications retain their own support or limitations for live DPI changes. No host desktop settings are modified.

`subscribe_display(listener)` immediately supplies an immutable snapshot and
returns an unsubscribe function. Subsequent notifications occur only when the
resolved display configuration changes, including viewport and monitor changes.
The snapshot contains `available`, `policy`, `density`, `width`, `height`, and
`limit` (`null`, `display`, or `density`). Dimensions describe the configured
client backing surface, not a remote application's paint acknowledgement or
toolkit support. Consumers must not describe this as measured frame resolution.
`display` means the advertised remote dimensions reduced native density;
`density` means the four-times safety limit did. Disconnect revokes subscriptions.
Reading or subscribing sends no packets, starts no polling, and never requests
a quality refresh. Consumers select localized presentation, not scaling rules.

The SDK owns this rendering and input coordinate contract, not a picture-quality
policy. Consumers must explain that extra pixels cost bandwidth and encoding time;
native density is not a guarantee of low latency during continuous motion. Viewer resources have an independent lifetime; see the snapshot contract below.

Source tests execute both prepared clients, including DPR changes, server size
bounds, shadow cursor geometry and disconnect disposal. Native release qualification
runs `TestNativeClientInput` at density 1 and `TestNativeDisplayInput` at density 2
with managed and system Xpra on both architectures. GTK receipts also assert its
actual backing scale and unchanged logical text DPI. Both runs retain the complete
Unicode, focus and clipboard assertions below.

The authenticated input scheduler wraps the handlers actually registered by
each supported Xpra version, including 6.5's canonical pointer, keyboard and
window names. Legacy packet aliases resolve to those same handlers before
dispatch; they cannot move focus, close a window or deliver later input ahead
of a pending confirmed-text transaction. Native qualification records packet
types at the scheduler boundary, never their input bodies, and verifies that
real window focus takes this path on managed and system installations.

### Window layout and viewer snapshots

`set_window_layout(wid, "viewport" | "dialog" | "native")` makes the SDK the
single geometry owner for a managed window. Viewport and dialog layouts honor
minimum, maximum, base and increment hints in remote coordinates. Only viewport,
density, policy, decoration offsets or size hints trigger a new layout request.
Remote geometry updates are accepted without re-entering layout negotiation.
A native minimum larger than the viewport is retained, never fought by repeated
resize requests. Popups and override-redirect windows retain native placement.
Destroy and disconnect revoke layout ownership. Layout never changes actual
maximize/minimize state. Both regular and worker canvases preserve painted pixels
through resize and ignore unchanged sizes.
The offscreen decoder has one ordered decode/paint queue. It acknowledges damage
only after drawing into the canvas, and retires late decoded images on window
removal. Skipped video and no-op packets retain transport acknowledgement without
authorizing first-frame input. Browser compositing and actual native application
pixels remain separate acceptance evidence; decoding alone is insufficient.

`PrepareViewer(originalHTML)` produces an immutable `PreparedViewer` using the
same reviewed v20/v21 transformation as `PrepareInputClient`. `Document` and
`Assets` must be served together for each sharing connection. Preparation errors
are viewer failures; do not fall back to an application's historical `www` tree.
A new share may prepare current SDK resources while retaining the application's
PID, input modules, backend and instance directory. The snapshot outlives its
source directory and cannot be changed by a caller. Resource authorization and
no-store session/document responses remain the host's responsibility.

The prepared `floeXpraViewer` exposes `getClient()` and `capabilities(client)`.
Display reports `native` only for display protocol 2, otherwise `logical`.
Input protocol remains 1. Input reports `ready`, `unsupported`, `unavailable`, or
`restart-required`; pointer availability follows the same ordered-input boundary.
Only an authenticated incompatible process-module registration can produce
`INPUT_MODULE_VERSION_UNSUPPORTED` and `restart-required`. Missing contexts,
transport loss and other input errors never imply an application upgrade.
Hosts disable affected input and keep usable pictures/local controls. Corrupt
current resources fail viewer preparation rather than requiring an app restart.
Graphical services and loaded modules are not hot-swapped.

`TestNativeViewerLayout` runs prepared clients against live Xpra with GTK3,
GTK4, Qt5 and Qt6 minimum-size constraints. It records native workarea, accepted
geometry, command counts and application PID across density switches and viewer
reattachment, with regular and worker canvas paths. It is part of both managed
and system Xpra release qualification. A local Linux host that cannot sandbox
a downloaded Chromium may use a task-owned sandboxed Playwright server through
`FLOE_TEST_BROWSER_WS` and private loopback tunnels; never disable sandboxing.

`TestNativeViewerUpgrade` starts the published v0.7.0 backend/input fixture and
its preparation-v1 viewer, enters unsaved text, then attaches the current
`PreparedViewer` snapshot. It asserts the same application PID and contents,
continued Unicode input, and logical-only display capability. The fixture's
original `www` and loaded modules remain unchanged. `qualification/legacy`
consumes the released module with its checksum; this is an explicit native
release fixture, not a second production implementation.

### Cursor and input

`PrepareInputClient` also prepares one connection-owned cursor path for reviewed
HTML v20/v21. Remote PNGs preserve their shape and alpha, with a maximum longest
edge of 24 CSS pixels and no enlargement of smaller images. Hotspots scale with
the image. Integral backing density (ceil DPR, bounded to 1 through 4) is declared
through CSS image-set; it changes resolution, never logical geometry. Malformed
metadata, images over 1024 pixels per edge or 5 MiB encoded, and failed decodes
reset to the system cursor. Reset, disconnect and newer packets revoke unfinished
decodes. Existing shares pin their immutable resources; preparing a new viewer does not
rewrite live application modules or directories.

`CursorClientSource` exposes the same normalization owner for canvas viewers.
`FloeRemoteCursor` accepts one apply callback and `{width, height, logicalWidth,
logicalHeight, xhot, yhot, png}`. Native image size is independent of surface size;
buffer scale must never enlarge CSS geometry. Explicit `hide` and `reset` retain
distinct hidden/default semantics and invalidate unfinished decodes. Xpra has only
a packet/window-list adapter around this owner.

The unpublished combined backend observes the native pointer's cursor and focus
signals. It copies the current image only after the renderer's frame signal;
copying at surface commit can pair new geometry with an old renderer buffer.
The shell applies the surface's buffer transform once, preserves pixel density,
converts premultiplied pixels to straight RGBA and excludes the cursor from the
application frame. One current snapshot is transferred over the existing native
control channel in 1024-byte pull chunks. A newer revision or scene invalidates
unfinished reads. The helper exposes a bounded PNG on the existing authenticated
attachment, with at most one cursor payload in flight and only the latest pending
snapshot. Cursor updates never authorize input or acknowledge an application frame.

`FLOE_PROBE_CURSOR=1` adds known PNG cursor tiles to the persistent Chromium
fixture. Acceptance compares actual received RGBA bytes, native logical geometry,
application click coordinates, explicit hide and decoded frames without burned-in
cursor pixels. Source tests cover scene retirement, stale chunks and peer pressure.
These receipts supplement, rather than replace, actual system-pointer inspection
in the final consuming viewer.

The source gate runs deterministic geometry, ordering and lifecycle tests against
both original client fixtures without installing a browser. Release qualification
additionally runs the real PNG decode, CSS image-set, alpha and DPR matrix:

```sh
npm ci --prefix qualification --ignore-scripts
node qualification/node_modules/playwright/cli.js install chromium firefox webkit
FLOE_TEST_CURSOR_BROWSERS=chromium,firefox,webkit \
FLOE_TEST_CURSOR_EVIDENCE=/absolute/disposable/evidence \
  go test -count=1 -run '^TestBrowserCursor$' -v .
```

These browser assertions do not claim to capture the operating system cursor.
Consumer acceptance must also check actual pointer appearance, hotspot clicks,
page zoom and monitor changes in the shipped viewer and Desktop.

The source gate tests ordering/revocation and executes the prepared Xpra v20/v21
JavaScript with its actual keymap tables. It installs no browser. Native
qualification additionally requires GTK3, GTK4, GNOME Text Editor, PyQt5, PyQt6, xterm, and a native
Chromium executable with its normal sandbox available:

```sh
FLOE_TEST_INPUT_ROOT=/absolute/private/components \
FLOE_TEST_CHROMIUM_BIN=/absolute/native/chromium \
FLOE_TEST_INPUT_EVIDENCE=/absolute/disposable/evidence \
  go test -count=1 -run '^TestNativeClientInput$' -v .
```

Every fixture gets its own display, authenticated loopback WebSocket, bus,
process group, browser profile and receipts. Acceptance compares actual app
contents after 40 Unicode/Enter pairs and one 15,000-byte Unicode commit. Editors
also verify selection replacement, deletion, and 12 alternating pointer focus
changes against the exact contents of each field, then native paste, copy and
cut against the exact Unicode selection and resulting document. XIM fixtures use a UTF-8
locale; the bridge advertises only UTF-8 locales to prevent Xlib from silently
choosing a non-Unicode conversion context. Sending
requests or receiving protocol acknowledgements cannot pass alone. Screenshots
and process/version records accompany receipts. `FLOE_TEST_INPUT_FIXTURES` selects
a focused development subset; releases must leave it unset and run every fixture
on both architectures. These tests do not certify real client IMEs or mobile
keyboards.

Client-resource regression tests execute the prepared v20/v21 protocol and both
decoder worker creation paths with asynchronous document loading. They cover
content-version invalidation, conditional/compressed HTTP responses, excluded
private files, and escaping assets. Native input qualification additionally opens
the real installed prepared client as a resource snapshot. Consumers must verify
their actual authenticated route, browser cache reuse and rendered input after
integration; serving cached resources must not bypass application admission.

The same test also runs with `FLOE_TEST_INPUT_SYSTEM=1` against system Xpra
6.5.3 from its publisher's signed Ubuntu repository. This selects the installed
Xpra interpreter, Xvfb and D-Bus while retaining the managed protocol client as
the receipt driver. Both server installations use the same input adapter and
application assertions on both architectures. Installation of system fixture
packages belongs only to disposable test hosts, never production preparation.

The prepared v20/v21 client also exposes the versioned external `floePointer`
adapter. Content canvases have no Xpra mouse, touch, compatibility-mouse, or wheel
owner; the shared client controller delivers movement, buttons, and CSS-pixel
scrolling through the adapter. It retains connection/window/canvas identities and
the first-frame gate, preserves decoration move/resize handoff, and clears wheel
remainder on cancellation or target replacement. Pointer qualification consumes
the published `@floegence/floe-webapp-core` package. Native fixtures assert actual
GTK and Chromium scroll positions, nested scrolling, taps, double taps, right
clicks and slider dragging. Browser event tests do not certify physical iOS,
iPadOS or Android devices.

Hosted qualification installs the downloaded Chromium build's own setuid
sandbox helper in the disposable runner, following Chromium's documented test
bot setup. It never disables the browser sandbox or the host's AppArmor policy.
This provisioning belongs only to the fixture; product runtime preparation does
not install privileged components or modify application sandbox settings.

Build first-party modules natively with `scripts/input_builder.Dockerfile`, using
the original architecture-specific Debian digest in the existing build record
and `QT_MAJOR=5` or `6`. Copy the fixture host's certificate bundle to the ignored
`input_modules/certificates/` directory. Run `python3 scripts/fetch_input_toolkits.py` to acquire and verify the original
GTK/Pango archives into ignored `input_modules/sources/` before building. The
image verifies those bytes again before extraction and uses the signed 2025-02-24
Debian snapshot. Mount this repository at `/src` and run
`scripts/build_input_modules.sh /absolute/output 5` (or `6`) in that image with
`FLOE_INPUT_BUILDER_IMAGE` set to its original pinned Debian digest. Commit the
binaries and provenance together; source hash drift fails the gate. No toolkit
binaries are copied into these modules. Containers are build/test fixtures only.

### GTK4 and click-to-keyboard qualification

Prepared client bootstrap version 2 removes the intermediate window mousedown
interceptor. `TestBrowserInputFocus` constructs both reviewed Xpra windows with
real jQuery UI, clicks pixels and types without test-forced focus. It runs in
ordinary documents and iframes in Chromium, Firefox and WebKit, including local
control focus and decoration drag. Run it with `FLOE_TEST_POINTER_BROWSERS` and
retain receipts through `FLOE_TEST_POINTER_EVIDENCE`. A prototype-only fixture
cannot exercise constructor event ownership and is not equivalent evidence.

Native input qualification includes GTK4 TextView and Entry contexts. Entry
assertions omit line breaks because it is a single-line widget; all other Unicode,
ordering, focus, editing and clipboard assertions remain. The GTK4 module is a
GIO `gtk-im-module` extension in the private `gtk/4.0.0/immodules` directory, not a
generic GIO extension and not a GTK3 cache entry. Text wire protocol version 1,
marker ordering, private-bus ownership and application acknowledgements are unchanged.
GTK4 selects XI2 only: its focused input context consumes the same core marker
through GDK’s native `xevent` signal. The sender addresses the native window owner
without requiring core event selection. The context verifies the marker belongs
to its surface, including the private focus child used by older GDK versions.
No second injection path or changed
keyboard mapping is introduced. GTK4 type registration uses the actual runtime
parent sizes because newer GtkIMContext classes exceed the original 4.0 layout.
`XpraArgs` also carries the private GTK path through `FLOE_NATIVE_INPUT_GTK_PATH`
for `WriteApplicationLauncher`: support Python clears ordinary GTK search paths,
so the launcher applies this explicit input setting after restoring the host map
and removes the handoff variable before executing the application. Installed
lifetime checks assert this final child environment. Native application fixtures
use this same GIO launcher, including the minimum GTK runtime qualification.

The Qt5/GTK build fixture compiles pinned GTK 4.0.3 and Pango 1.48.10 on Debian 11 because the
snapshot has no GTK4 development package. The original GTK archive is verified
before extraction and retained in provenance; its libraries are never shipped
with adapters. For minimum-runtime acceptance, compile `qualification/gtk4_baseline.c`
against `/opt/gtk4` inside that same native fixture and set
`FLOE_TEST_GTK4_BASELINE` to its absolute executable path while selecting the
`gtk4` native input fixture. This runs the same actual document, focus and clipboard
assertions with GTK 4.0.3 and glibc 2.31. GTK4's normal system-library qualification
must also run on both native release runners. Neither run certifies real client IMEs.

Focused GTK4 contexts retain their native marker subscription when GtkTextView or
GtkSourceView reasserts the same client widget. A different widget, focus loss or
disposal still revokes it. The native `qualification/gtk4_context.c` fixture covers
same-widget rebinding and revocation on both the GTK 4.0 baseline and current GTK.
The standalone GNOME editor qualification uses private XDG directories and verifies
save followed by repeated Unicode, selection replacement, multiline text and Enter
against bytes saved through the application. Both managed and system Xpra run it;
no user editor is reused. Browser composition remains simulated.

### Unified native component build candidates

`qualification/desktop_compatibility/Dockerfile.desktop-native` is the single
musl builder for the shell/capture, reviewed Weston derivation and private IBus.
It verifies and retains every original builder APK before offline installation.
`scripts/build_desktop_native.sh` verifies the two original source archives,
rebuilds every affected artifact, uses Weston's normal staged installation to
remove build loader paths, and rejects ELF architecture or RPATH mismatches.
The resulting manifest covers original sources, reviewed patches, build scripts,
compiler, signed dependencies, licenses and final binary bytes. Qt modules retain
their separate glibc ABI baseline builds because they load into applications.

`fetch_candidate.sh` acquires the original runtime APK closure in that disposable
builder, including private Python Xlib and XIM dependencies. Its candidate catalog
pins original bytes and license/source metadata without activating a package.
Neither a successful build nor an archive manifest establishes package support:
native installed-helper application, cancellation/recovery and exact-release
qualification remain required before production catalog publication.
