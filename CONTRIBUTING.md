# Contributing

Floe Native Apps owns native component preparation. Keep application inventory,
product authorization, viewers, and application sessions in the consuming host.
Read [AGENTS.md](AGENTS.md) for the implementation and repository boundaries.

## Development

Use Go **1.27.2**, Python 3, Node.js 20 or later, Git, and a POSIX shell. The Go version in `go.mod`
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

Prepared Xpra viewers can use `DocumentWithOptions` with a same-origin
`TransportScriptURL`. The script must synchronously supply
`globalThis.floeHostTransport.WebSocket`, a WebSocket-compatible constructor
owned by the authenticated host. The document requires this transport before
any client initialization; failure never falls back to a native socket or
WebTransport. Only protocol processing moves to the document realm. Offscreen
and image decoding workers retain their independent capability negotiation.
The transport script, authentication, resource access and connection lifetime
belong to the host. Default `Document` consumers keep the existing transport.
`TestClientAssetsRewriteOnlyPublicReferencesAndKeepWorkersWithScripts` executes
both reviewed client versions with a required carrier, missing carrier and
active decoding worker; `TestPreparedViewerHostTransportIsExplicitAndPrecedesClient`
verifies script origin and initialization ordering.

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

The system Xpra fixture pins every required split package to version 6.5.3-r0-1.
`qualification/system_xpra` retains the publisher's original signed Noble Release
from 2026-09-27, complete architecture indexes and public signing key from
`https://xpra.org/xpra.asc`. `scripts/prepare_system_xpra.py` verifies that signature,
each complete index and each original publisher download before installation in
a disposable runner. Local gzip encoding only compresses the original index bytes.
Live mirror synchronization and newer split packages cannot silently change this
fixture; integrity errors still stop qualification. Production acquisition and
the managed component catalog remain unchanged.

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

Catalog revision `r4` refreshes the original publisher pins for Python 3.12,
PCRE2, Expat and libcrypto after Alpine retired older archive URLs. Both native
architectures must verify publisher signatures and qualify the unchanged
graphical contracts. Installed `r3` recipes retain their exact identities and
remain available to surviving processes; an update never rewrites their files.

Catalog revision `r5` completes that closure with the matching OpenSSL libssl
and Python bytecode metapackage pins. A real GET request exposed a retired arm64
libssl archive despite a cached successful HEAD response. Publisher signatures
and complete acquisition, rather than HEAD responses, are required evidence.
Installed `r4` recipes keep their original identities and files.

Catalog revision `r6` refreshes libpng to the publisher-signed 1.6.59-r0 archives
after the original 1.6.58-r1 URLs were retired. Both architectures retain exact
installed `r5` identities, licenses and source provenance. The resolved physical
desktop media archive can exceed one GiB because library aliases become regular
files; producer and privileged extractor share a two-GiB expanded budget, a
256-MiB per-file limit and a 512-MiB compressed limit. Links and special files
remain forbidden, and canceled or invalid exports are removed before deployment.

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

### Catalog-backed preparation

`DesktopForPlatform` returns the combined Wayland/Xwayland recipe. `ForPlatform`
retains the published Xpra recipe and its exact digest. Both use the same
`Manager` for bounded download/upload, original archive verification, extraction,
cancellation, restart and atomic activation. The new preparation contract binds
the embedded native manifest into the component identity; an unknown contract
cannot activate. The default graphical self-check requires decoded fixture
pixels, exact repeated Unicode plus Enter, reconnect with the same application
PID, ordinary key input and normal window closure. The original package includes
fonts, MIME data and image loaders so a host desktop does not mask dependencies.

`Manager.DesktopBackend`, `PlanDesktop` and `PrepareDesktopSession` bind an
authorized desktop entry to the verified installation. Preparation revalidates
the immutable plan, creates one new private source/configuration snapshot and
returns the executable and authenticated endpoint. The consumer launches this
helper through its persistent process owner; viewer cancellation must not own
the process lifetime. Existing directories are not rewritten, environment values
are transferred exactly, and the socket directory must already be owner-only.
The helper verifies those boundaries again and the supervisor revalidates the
application immediately before GIO execution. No input readiness follows from
successful preparation.

A current Manager can retain an older Xpra installation for a surviving
application; it cannot advertise that installation as a combined backend.
Updates never replace loaded modules, live instance files or user processes.
`ResolveDesktopTools` checks contained original tool paths and the bounded
native file manifest without executing an application. The original runtime
environment is restored only at the existing GIO launch boundary.

The explicit engineering CLI selects recipes for qualification/offline download,
not as an application graphics-mode setting:

```sh
go run ./cmd/floe-native-apps -recipe desktop -state /absolute/new/private/state
go run ./cmd/floe-native-apps -recipe desktop -check /absolute/installed/root
```

The combined recipe remains subject to the complete native application/package
release matrix. `TestNativeDesktopInstallation` can consume previously verified
original candidate APKs using `FLOE_TEST_DESKTOP_INSTALL_CANDIDATE` and a new
`FLOE_TEST_DESKTOP_INSTALL_STATE`. It goes through the actual Manager activation
path and retains only fixture-owned pixel/document/exit receipts. A source-only
test or a successful GTK installation fixture is not a Snap/Flatpak or desktop
environment support claim.

### Standalone image decoding

Desktop recipe r3 adds the original signed Alpine librsvg and libdav1d archives
on amd64 and arm64. Preparation queries the component's own GdkPixbuf loaders
and writes a relocatable cache before activation. Standalone support Python
always selects this cache and its private module/library directory; host loader
overrides cannot substitute for missing component dependencies. The installation
self-check decodes a known SVG, verifies pixels and round-trips PNG before opening
the graphical fixture. This capability also applies outside a desktop session.

The native binaries and their provenance are unchanged from r2. The exact r2
recipe identities retain their original wrappers and need no cache rewrite.
Surviving applications keep their installation; new launches require activation
of the current recipe. Application inventory and theme selection remain owned by
the consuming product.

### Attachment boundary

`DialDesktop` attaches to an existing Linux helper through a private, versioned
Unix socket. A trusted application instance supplies its endpoint, identity and
token. The socket must be owned by the host user, mode 0600, in a mode 0700
directory; kernel peer credentials are checked before authentication. A successful
attach returns the native state and an immutable connection generation. Invalid
authentication must not displace the current viewer.

`DesktopConnection` has one reader and serializes concurrent writers. `Send`
returns a request ID, not an application receipt; `Read` returns native events and
correlated replies. Metadata and encoded image payloads are bounded and read as one complete
event. The consumer must decode and paint a frame before sending `frame_ack`.
Input always names its original connection, window and geometry generation. Scene
retirement returns an explicit error for every cancelled request on the current
attachment; callers never wait indefinitely or replay input into a replacement
target. Detach cancels silently on the retired connection only. The
helper remains the sole owner of admission and input ordering. There is no client
queue, automatic acknowledgement, reconnect loop or replay. Cancellation during
I/O closes the partial stream. `Close` detaches sharing and never terminates the
helper or application. Waiting, unavailable capture and connection loss are not
application-exit evidence.

A helper advertising `stream_version: 2` accepts `configure_stream` with `auto`,
`clarity`, `smooth`, or `data`. `DesktopConnection.StreamVersion()` preserves that
capability for a host's authenticated transport. Do not send configuration to a
retained session without the capability. An unconfigured attachment retains the
whole-PNG, one-frame protocol. Configuration permits two ordered in-flight frames;
only the oldest paint receipt is admitted, and input remains tied to painted
native targets. This bounds latency without granting authority from received bytes.

Negotiated frames contain a bounded `x`, `y`, `region_width`, `region_height`
rectangle inside the full `width`/`height`. Partial frames name the preceding
transmitted `sequence` as `base`; full images have base zero. PNG preserves small
edits exactly. Lossless WebP compresses repeated desktop content; Auto and Smooth
may use a smaller JPEG for dense imagery. Clarity always remains lossless. Data
saver uses more compression effort and coalesces delivery to at most 15 FPS.
Unchanged source pixels send no image. After 250 ms without actual pixel changes,
lossy content receives a lossless full refinement. Compositor commits with
identical pixels cannot postpone refinement. The picture mode changes neither
application geometry nor process identity.

`DesktopFramesClientSource()` supplies the ordered browser compositor. Hosts pass
the current target validator and acknowledge only its `onPaint` callback. Retired
decodes close their bitmaps without painting; at most one decoder and two current
target packets exist. Capture failure retires outstanding receipts on both ends;
recovery starts with a full image. Scene changes, rejected captures, refresh and
attachment replacement invalidate the image reference independently of input.

Recipe r2 changes the private capture ABI. Published r1 installations remain
recognized for surviving application resources and attachments. New sessions
require explicit activation of the current recipe; the SDK must never pair r2
helper code with an r1 capture binary. Removing r1 resource recognition requires
an explicit future compatibility release decision.

### Browser launch isolation and performance evidence

A trusted host may set `ApplicationPlanOptions.BrowserProfileDirectory` to a
canonical absolute, persistent owner/application-scoped directory. Planning binds
recognized native/deb/rpm Chromium and Firefox launchers; an explicit profile
argument remains authoritative. The launcher creates or reuses only a private
0700 directory owned by the current user. It preserves original Desktop Entry
arguments and inserts profile flags before an option terminator. Personal profile
contents and singleton locks are never read, copied or removed. Sandboxed package
launch contracts are unchanged. If the observed process tree exits before its
first native window, exit zero reports `APPLICATION_NO_WINDOW`; a live slow-starting
process is still allowed to wait for its real window.

`qualification/desktop_compatibility/stream_session_probe.py` creates a task-owned
browser/profile/document and measures first frame, three seconds of idle, twenty
keyboard-to-pixel changes, five seconds of motion, and settled refinement. It
records payload bytes, FPS, median/p95 host input-to-decoded-pixels latency and
errors. Run Chrome and Firefox sequentially on the same host and size. Compare a
published baseline using `--baseline --mode legacy` and its own installed state,
then run every negotiated mode using the candidate's public launch executable.
Never compare overlapping workload runs or label a new encoder's legacy mode as
the old release. The probe cleans up only its own supervisor tree.

These are host pipeline measurements, not browser presentation or WAN latency.
A consuming product must additionally measure its real viewer's paint callback,
wire bytes, reconnects, keyboard/pointer continuity, decode queue bounds, and p95
latency under recorded RTT/bandwidth conditions. Preserve exact commits, platform,
resolution, mode, timings, and raw results. Do not claim video-codec parity or
mainstream remote-desktop equivalence from a static-document fixture alone.

`terminate_application` is reserved for explicit, authorized force-quit intent.
It accepts no target, PID, signal or operation payload. The installed session
signals only its own existing supervisor; that supervisor retains descendant
ownership and writes the actual termination receipt. The reply `requested` is
not proof of exit. Pending input is cancelled before lifecycle dispatch. An
unavailable graphics backend permits observation-only reattachment and explicit
termination, without restoring any target or input authority. Neither capture
loss nor detach invokes termination. Native qualification checks running and
windowless applications, plus reattachment after capture and private-bus loss.
After its final child wait, the supervisor ignores late termination before
publishing the exit receipt, preserving that result through interpreter shutdown.
Applications may choose to exit themselves when their private bus fails; the
host reports their actual exit. The bus-loss fixture explicitly retains its GTK
connection to distinguish that toolkit policy from host-initiated termination.

`release_input` binds connection, window and geometry like ordinary input. It
cancels pending input and releases held native keys/buttons while retaining the
already-painted target. Stale callbacks cannot release a replacement target.
The keyboard controller may use it on blur; cancelling only a pointer gesture
still sends individual button releases without interrupting composition.
The helper records explicit termination before revoking capture. Support
disposal during that stage cannot overwrite the supervisor's actual exit with
a capture failure; failures already observed before termination remain recorded.

Confirmed text has one 30-second native completion deadline shared by Xpra and
the combined backend. A long edit may keep a real editor's event loop busy for
more than three seconds. Following input stays queued until the actual native
completion; a deadline only reports failure and revokes it. It never implies
success, retries text or releases the queued Enter. Qualification records exact
saved bytes, including original mounted AppImages, as the application evidence.

The private software output grows to contain the selected native window family
and returns to its 1000 by 700 baseline after oversized dialogs close. The same
bounded output dimensions drive capture, Wayland and Xwayland pointer positions;
view scaling cannot substitute for resizing X11's actual coordinate space. Output
changes retire the painted scene and require a newly decoded frame before input.
Native oversized-dialog qualification checks both opposite-corner controls and
the restored parent frame on Wayland and X11. Dimensions above 4096 pixels revoke
sharing without terminating the application.

A live native subsurface may outlast its parent and still issue a commit.
Such a detached surface has no application-window identity and cannot become
an input or capture target. Native Snap qualification opens Firefox's menu
after an actual Unicode save and closes the parent window with the menu open;
it requires normal application and compositor exit, not only a close request.

`TestDesktopClient*` exercises fragmented packets, malformed framing, cancellation,
concurrent writes and the real Python control/attachment boundary on native Linux,
including takeover and late old-owner cleanup. Its synthetic native callbacks are
wire qualification only. This attachment API does not prepare a compositor or
launch an application. The catalog-backed preparation API below owns those
resources. The complete package/desktop application matrix remains a release
prerequisite; wire tests alone cannot certify a supported graphical backend.

The `Private desktop` release jobs acquire the combined catalog on both native
architectures before installing host test toolkits. The same distribution script
accepts `NATIVE_RECIPE=desktop` and exercises the installed component in clean,
unprivileged distribution fixtures. `TestNativeDesktopInstallation` downloads
original publisher archives when only `FLOE_TEST_DESKTOP_INSTALL_STATE` is set;
the optional candidate path supplies already verified archives for offline tests.

`scripts/qualify_desktop_applications.py` runs the installed public preparation
API with GTK3/GTK4/Qt5/Qt6, Chromium and lifecycle failures, followed by strict
Snap Firefox and Flatpak GTK/Qt input/save/exit cases. Missing prerequisites and
failed application receipts fail the job. Artifacts retain fixture pixels and
documents, excluding source working directories with helper credentials and
application profiles. These jobs are necessary release gates; desktop environment,
AppImage, RPM and classic Snap claims still require their corresponding actual
application evidence. They are never inferred from these suites.

Disposable private-desktop runners retain a compositor backtrace when a recorded
native compositor exits with SIGSEGV. Core capture is limited to fixture process
trees and the runner's temporary directory. Only stack addresses and mapped
libraries are published; raw process memory, application cores and launch
environments are excluded. A crash remains a failed qualification even when
input and saved-file receipts already passed.

The Flatpak GTK fixture observes the private IBus daemon's real focused,
synchronous context before its initial click/text burst. A painted window alone
does not prove an editable context. It also waits for the exact completed GNOME
draft before Save As, excluding GIO temporary files: GNOME rejects Save As during
autosave. These are read-only fixture observations, not production delays or
text retries. Actual Save As bytes, document grants and normal exit remain required.

The internal `DesktopHelper` composes the native channel/window registry, bounded
capture, decoded-frame gate, ordered input and authenticated endpoint on one event
loop. The persistent launch owner supplies its prepared sockets. It starts sharing only after
the native protocol is identified and owns cleanup if endpoint creation fails.
Viewer detach leaves the registry and capture process intact; native channel loss
reports unavailable state without inventing application exit. Full helper disposal
closes its channels, while process lifetime remains with the launch supervisor.
Authenticated mixed-window probes use this same assembly rather than a second test
implementation. Complete package admission and installed-component activation remain separate release prerequisites.

Window close remains ordered after confirmed text, but it addresses the live
native window instance and authenticated connection, independently of the painted
scene. A save dialog or selection may retire pixel coordinates without retiring
the window. The compositor rejects old connections and retired window instances;
it never substitutes the currently selected window. A `requested` reply records
submission only: native qualification requires the actual application's normal
exit or its visible save/cancel dialog. Pointer and keyboard input still require
the current decoded frame and scene generation.

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
instance/token and caller-verified compositor, capture, input-service and toolkit adapter
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
Component-owned GTK services and the installation widget use the component's
verified XKB data explicitly. A clean distribution need not install keyboard
tables at libxkbcommon's compiled system path. The application handoff restores
the host application's original XKB environment; no global search path changes.

The entrypoint revalidates the plan before preparing resources and delegates all
processes to `DesktopSession`. Its bounded `desktop-status.json` is an atomic
receipt of that owner's native transitions/process identities, not a second
lifecycle manager. Its writer retains the current file descriptor until atomic
replacement or shutdown, so inode reuse cannot authorize overwriting another
file. It records prepared graphics digests and distinct content-free
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
when the private bus or Xwayland connection is already closed. The compositor-loss
case observes GTK's own display-loss exit and requires the helper to release all
remaining services; it never infers application exit from channel loss alone.
Qualification checks every recorded child
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
explicit initial files; it never exposes the host desktop bus. Before binding
its unique host peer, it requests official D-Bus activation of the fixed document
service. A missing, failed or unsupported host document service produces
`DESKTOP_HOST_SERVICE_UNAVAILABLE` before application execution; no package is
installed and no alternative service is substituted. Disposable qualification
runners install `xdg-desktop-portal` explicitly because Flatpak only recommends
it on Ubuntu. An installed but dormant service works without a prior desktop
login; unavailable activation remains a failure and never starts a replacement
document authority. Full exports remove only the official `AS_NEEDED_BY_APP`
optimization: installed package permissions cannot prove access for an instance
started with stricter filesystem restrictions. The actual service issues the
selected-file grant, preserving reuse, persistence, directory semantics and
requested permissions. No path is retried, fabricated or granted broader access.
Qt registration
uses the package's existing allowed bus namespace. The helper copies the verified
Qt5/Qt6 modules into one unique, owner-only directory inside the package's normal
`~/.var/app/<id>` root. It does not alter application profiles or sandbox permissions.
That resource root must be owned by the current user and not writable by another
account. Unsafe existing roots are rejected without changing their permissions;
disposable qualification users must provision private package resource roots.
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

### Combined-display qualification

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

The persistent helper transports the native clipboard as bounded UTF-8
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
For an X11 target, the exact XFixes ownership notification completes publication;
enqueueing the XWM request alone cannot order its separate X socket against native
seat events. A superseded source, target change, XWM loss or bounded timeout rejects
the pending publication without replay. The bridge never logs selection bodies.

`session_probe.py` modes `clipboard`, `clipboard-x11` and `clipboard-chromium`
exercise GTK3/GTK4/Qt5/Qt6 Wayland/Xwayland and pure Wayland Chromium Copy/Cut/Paste,
oversized source rejection, 11,400-byte Unicode selection transfer, actual
document bytes, decoded final frames and detach/reconnect. The GTK fixture samples
the empty second editor because the long pasted document occupies the original
sample pixel. Select a toolkit with `FLOE_PROBE_TOOLKIT`; Qt records the exact modules in
`FLOE_PROBE_QT_PLUGINS`. Native arm64 results do not qualify amd64,
product clipboard permission handling or the complete distribution.
The native release suite also pauses its own pidfd-verified Xwayland during
publication and immediate Paste. This scheduling fault injection must preserve
the same exact document bytes; the pause is not a production delay or readiness
heuristic.

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
separate acceptance evidence. The combined helper admits one
text-input-v3 resource for the actual focused surface and live application peer.
Its existing input scheduler brackets native commit dispatch with callback
barriers. The compositor alone selects and validates the editable context when
it sends the text, so stale helper copies of enable/disable state cannot reject
ordered focus changes or authorize another resource. Revoked connection, window,
surface and resource identities cannot receive late text or advance a new
transaction. Native rejection is final; no alternate adapter or replay follows.
Only actual application receipts establish document content and order.
Native binaries and adapters enter the combined catalog through the verified
distribution manifest. Each release still requires installed-helper application
receipts and the complete native qualification matrix.

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
service owners remain `DesktopSession` responsibilities; preparing these files
alone does not establish application readiness.

`Dockerfile.qt-native` selects an explicitly pinned native `TARGET_ARCH` and
`QT_MAJOR`, then verifies Debian's signed 20250224 snapshot. Qt 5 uses the 5.15.2 /
glibc 2.31 baseline; Qt 6 uses 6.4.2 / glibc 2.36. `build_qt_native.sh` records the
immutable builder identity, signed archive closure, source hashes, ELF requirements
and module digest. It installs no host or sandbox libraries. The qualification-only
`prepare_qt_baseline.py` exports an actual widget application and its exact baseline
library closure through a private loader. Its document and runtime receipts prove
the library versions actually executing; they are not inferred from linker flags.
The same adapter is also tested with the normal distribution and Flatpak runtimes.
The combined catalog binds these adapters and their build provenance; baseline
receipts supplement the installed-component release qualification.

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
After an accepted remote native resize, the display adapter requests one X11
Expose for the accepted drawable dimensions. A shrinking redirected GL surface
can lose its pixels without exposing a new edge; Xpra's synthetic capture damage
does not ask the application to redraw. The request follows native configuration
on Xpra's existing connection, with no timer, retry, renderer override or second
capture path. Moves, unchanged sizes, rejected old counters and retired or hidden
windows do not request a repaint. Ordinary application damage still owns delivery.
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

The combined backend observes the native pointer's cursor and focus
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
The native receipt driver waits for actual toolkit allocation and Xpra geometry
to agree before its unsplit focus-change burst; a requested size is not a native
allocation. Pointer qualification asserts complete swipe delivery and no further
controller output after release, separately from the application's asynchronous
wheel animation. Chromium's final scroll receipt must include the entire swipe.

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

### Corresponding source and modified native builds

The module includes complete original Weston and IBus source archives, their
reviewed patches, source notices and all first-party native build sources in
`native/dist/common`. Each architecture's `manifest.json` identifies the exact
installed source and binary files; `provenance/native.json` and `qt5.json` /
`qt6.json` record build inputs and signed dependency versions. The original
Wayland protocol XML used to generate shell code is included with its license.
Published module bytes and original APK checksums remain immutable.

To rebuild or modify the native binaries, use a source checkout matching the
module (or copy the accompanying `sources/tree` into a clean build tree). On
each native architecture, build the disposable Alpine image from
`qualification/desktop_compatibility/Dockerfile.desktop-native`, preserve its
image identity and signed package list, and run:

```sh
FLOE_DESKTOP_BUILDER_IMAGE=sha256:YOUR_RECORDED_IMAGE \
  sh scripts/build_desktop_native.sh \
  /absolute/sources/weston-14.0.2.tar.xz \
  /absolute/sources/ibus-1.5.33.tar.gz /absolute/new/output
```

This command runs inside the disposable native builder with the source mounted
at its normal tree path. It applies the source modifications, generates protocol
code, builds and normally installs the libraries, strips the resulting artifacts
and verifies ELF architecture and loader paths. Build-system dependencies are
provided by the recorded original signed Alpine packages. Compare the builder's
protocol XML with `native/protocols/text-input-unstable-v3.xml`; a changed input
requires a new provenance review. Application hosts never execute build scripts
or install these build dependencies.

The Qt adapters are rebuilt in `Dockerfile.qt-native` on the corresponding native
architecture with `TARGET_ARCH=amd64|arm64` and `QT_MAJOR=5|6`. Supply the build
host's existing certificate bundle as `ca-certificates.crt`; Debian snapshot
signatures remain mandatory. Run `build_qt_native.sh` with the absolute `native`
source path, a new output directory and that Qt major, setting
`FLOE_DESKTOP_QT_BUILDER_IMAGE` to the recorded image identity. These adapters
dynamically resolve the application's Qt/Wayland libraries; no additional toolkit
runtime is copied into the distribution.

Native GTK3/GTK4 applications use `native/gtk_native.c`, loaded from a private
GTK3 cache and GTK4 ABI directory. It selects its route from the actual display:
Wayland delegates to the toolkit's negotiated `wayland` context; X11 registers
with the same native confirmed-text scheduler as Qt and consumes its native seat
marker. An idle completion follows the actual toolkit commit, and the scheduler
also requires the marker release. Host IBus GTK packages, global module installs
and host input caches are not prerequisites. Strict Snap and Flatpak retain their
runtime's IBus route without loading these host GTK adapters. Each context has
one route before text admission; no failed text is retried on another adapter.

Build these two adapters with `Dockerfile.gtk-native`, `TARGET_ARCH=amd64|arm64`
and the same fixture certificate input. It reuses the signed Debian 11 snapshot
and verified GTK 4.0.3/Pango source preparation used by Xpra's input adapters.
Run `build_gtk_native.sh /absolute/source/tree /absolute/new/output` inside that
native builder, setting `FLOE_DESKTOP_GTK_BUILDER_IMAGE` to the actual image hash.
`provenance/gtk.json` records signed archives, toolkit versions, source hashes and
ELF dependencies; GTK/glibc libraries remain in the disposable builder. Installed
native qualification must include GTK3 and GTK4 on both Wayland and X11 without
host IBus modules. The installation self-check's component-owned musl GTK fixture
uses its separate verified support IBus cache; it does not validate host ABI
adapters in place of native application qualification.
GTK3 may ship its Wayland context as a separate toolkit module or compile it
into GTK. The adapter selects the implementation from the loaded toolkit: its
normal module ABI for the separate build, or an explicitly selected public
`GtkIMMulticontext` with a verified native context type for the built-in build.
Neither path reads a host input-method cache or retries a text submission.
Fedora RPM Firefox qualifies the built-in form; Ubuntu GTK3 qualifies the
separate module on Wayland and retains the X11 marker regression.
The release job also exports a disposable GTK 4.0.3/glibc 2.31 runtime using
`prepare_gtk_baseline.py` and runs its real text widget through the installed
public session API. Only the test executable is relinked to the original copied
loader, preserving kernel auxiliary security metadata. No runtime library or
GLib security check is patched, and these libraries never enter the catalog.
The receipt records the actual runtime closure and module identities; text,
clipboard and Enter assertions use widget document bytes.

After building, `scripts/stage_desktop_distribution.py` consumes per-architecture
native outputs, Qt/GTK artifacts/build records, original source archives and verified
candidate catalogs. Its `--native`, `--qt`, `--gtk`, `--sources` and `--candidates`
arguments each name an absolute directory. Review and remove only the previous
generated `native/dist` before staging; the script refuses to overwrite it.
Use font-inclusive candidate catalogs named `amd64.json` and `arm64.json`.
Staging verifies source parity and produces new native manifests/component
identities; it does not activate or publish anything. A modified build must use
a new recipe identity, run the same source and native qualification and build
the consumer against that module. Do not repair manifests without rebuilding
the corresponding changed source, or replace library files in a running session.

The LGPL source and modification/relinking permissions described in
`THIRD_PARTY_NOTICES.md` apply independently of the reviewed-build integrity
checks. Hashes identify the build; they do not replace license/source obligations.

## Current-user desktop media

The current-user host desktop is distinct from an application's private display.
`HostDesktopForPlatform` supplies the Linux `host-desktop-media-v1` component through
`Manager`; neither the catalog nor installation authorizes capture. Its original
Alpine archives include GStreamer, PipeWire, Opus and H.264 encoders. The private
wrapper scopes their loader, GIO, GStreamer and PipeWire paths to this helper only.
It does not start a compositor, PipeWire server, session manager or system service.
`ResolveHostDesktopTools` verifies the embedded helper snapshot; consumers must
use the Manager's selected installation and must not rewrite that snapshot.

The Linux helper selects the active local graphical login owned by the current
user. Wayland uses the public RemoteDesktop/ScreenCast portal and its authorized
PipeWire descriptor. X11 uses the selected login's authenticated local X display.
Wayland absolute pointer coordinates use the live capture stream's unencoded
dimensions. Portal monitor geometry can use the compositor's scaled coordinate
space and must not be reused for stream input; encoder resize never changes input
geometry. A missing or retired capture cannot authorize pointer positioning.
Filter seatless SSH/service logins before reading their properties: their exit
cannot interrupt the graphical login. Missing or invalid graphical-session
properties still revoke authority instead of using a cached session.
When GDM leaves logind's display field empty, the helper resolves display and
private cookie-file location from same-user processes in that exact login scope.
Conflicting credentials fail closed; SSH and private application display
environments do not participate. X11 samples complete frames at the requested
cadence and filters unchanged pixels itself. Compositor damage aggregation must
not reduce scrolling capture to irregular updates. Clipboard wrappers are retired
before their GDK display; qualification covers a headless interpreter disconnect
and finalization as well as interaction inside a GTK fixture.
An unavailable backend does not select another display. The caller supplies an
existing private persistent `--state` directory and a separate inherited
`--media-fd`. Commands on stdin and control responses on stdout have independent
writers from media; all use a big-endian four-byte JSON-header length, the header,
and its declared binary payload. `WriteHostDesktopCommand` and
`ReadHostDesktopMessage` own this protocol. EOF revokes the attachment.

Portal restore grants are private and independent of the current desktop state.
`capabilities.authorization` and state events report `unsupported`,
`needs_consent`, `saved`, `restoring`, `revoked`, or `unknown`; `saved` means a
credential exists, not that the OS guarantees acceptance. Probe never starts a
sharing request or consumes a token. Hosts explicitly request persistence with
`unattended`; the SDK uses RemoteDesktop `persist_mode=2`. Older portals still
support temporary sharing and advertise persistence as unsupported.

Only one pending start or `forget_authorization` operation holds the grant lease.
A submitted single-use token is retained with an uncertain state but never replayed.
A replacement returned by Start is durably staged before PipeWire initialization;
valid streams and an opened descriptor commit it. Media failure or process restart
can recover the staged replacement. Failed writes fail the connection. Legacy v1
grants are read without mutation and migrate atomically on the next write; unknown
versions or invalid private files fail without replacement. Forget removes only
local credentials, never system permissions, and cannot race a pending start.

A Portal response code 1 means cancellation; code 2 is a generic failure, not
proof of revocation. Session Closed revokes live input/media authority but does
not prove that persistent OS permission was withdrawn. Report only observable
reasons; neither event discards a successfully stored successor grant. No retry
loop can make an uncertain submitted token reusable. The system may request
consent again after a consumed token's result is lost or restoration is refused.
The native grant lease does not replace the host's remote control lease.
The SDK never changes lock-screen policy. The current capability reports remote
unlock as unavailable. Lock and login changes retire frame authority, held input,
clipboard transfers and media. The GNOME 46 clipboard backend advertises MIME
arrays in a one-element tuple; the client accepts this observed `(as)` form and
the standard `as` form, without selecting a different clipboard backend.
A native generation and an actually drawn frame are
required before input or clipboard access. A consumer must additionally enforce
its authenticated session owner and the single remote console controller. A
client's paint acknowledgement is flow control and cannot replace those checks.

macOS consumers build the exact published Swift package `FloeNativeDesktop` into
their existing native helper. `NativeCaptureStream` supports application windows
and physical displays in the same process. The default one-frame application
contract remains available; desktop sessions use four credits and require H.264.
VideoToolbox selection encodes a synthetic probe at the actual requested size;
session creation alone cannot establish that the hardware accepts that size.
If only the system software encoder accepts the source resolution, the stream
reports `videotoolbox-software` and uses one frame credit. This preserves native
pixels with bounded latency; it makes no 60 FPS claim for that encoder or size.
On macOS 14 and later, static refinement uses ScreenCaptureKit's still-image
capture with the same authorized filter and dimensions. Video sample color
conversion is not a lossless still-image reference. A refinement is retired if
source pixels, capture generation or ownership change before it is delivered.
Application process/window ownership checks remain consumer-owned and mandatory.
`NativeDesktopSession` accepts an authoritative `mayControl` predicate and owns
native permission checks, display generations and release of its own held keys.
It does not authenticate a network client or create a second capture process.
Its explicit `paste` input writes the host text clipboard and posts the system
paste shortcut. It rejects held keys/buttons and releases its own shortcut on
failure. Consumers must disclose this clipboard mutation and obtain the user's
choice before routing client IME commits through it. Physical keys support the
host IME; generic Unicode key events and writable accessibility attributes do
not establish universal text insertion into applications such as Electron.
Complete or cancel the host's current IME composition before switching to client
text commits. An unfinished host IME may consume synthetic paste shortcuts;
native admission is not a receipt from the application's document. The SDK does
not send speculative Escape/Enter or invoke application menus to hide this boundary.

`HostDesktopClientResource` provides the WebCodecs player and AudioWorklet modules.
Consumers own authenticated control/media channels, published remote-input and
pointer controllers, clipboard consent/fallback UI, and all product controls.
Reset the player on each native generation and bind input only after its painted
callback. Hardware preference negotiation reports the selected preference; this
is not proof that a browser used a particular hardware decoder. A slow client
cannot grow the native frame pipeline, decoder queue or audio queue indefinitely.
Encoded H.264 dependencies are retained until an explicit recovery generation;
late PNG refinements cannot replace a newer video frame.
Decoder recovery keeps the authorized capture running and retires held input and
frame credits. Linux replaces only its encoder; macOS forces a new keyframe on
its existing encoder. Queued video, audio and refinements retain their original
generation and cannot authorize or overwrite the successor. Recovery starts a
fresh H.264 dependency chain.
Decoded pictures draw immediately, including while an earlier paint receipt is
pending. A receipt names only the latest picture unchanged between an animation
refresh and its following task; overwritten pictures receive no individual
receipt. The newest cumulative receipt releases older frame credits. Reset
retires every outstanding receipt. PNG decoding runs independently of ordered
H.264 dependencies, with at most four retained refinements.
Linux X11 and metadata-capable Wayland control sessions send bounded cursor PNG/shape/hotspot messages instead
of burning the host cursor into video. Cursor-only motion does not encode a frame.
The browser uses the local system pointer with that image; cursor messages never
grant input authority. View sessions retain the host cursor, composited from
PipeWire metadata on Wayland. Portals without metadata support and macOS retain
embedded cursor capture. Each painted frame declares `cursor` presentation;
consumers show a local pointer only after drawing a current separate-cursor frame.
Missing metadata in a negotiated metadata stream fails explicitly, never changes
that contract by guessing that the cursor was embedded.
Wayland input submits ordered, bounded asynchronous Portal calls so pointer
round trips do not block media and authority callbacks. Delivery failure or a
full input window closes the OS session and requires reconnect; input admission
does not claim compositor/application delivery. The same connection orders
releases before session closure. SPA VideoDamage reduces mapped comparisons and
skips unchanged copies only across contiguous header sequences with a retained
baseline. Missing metadata, corruption and discontinuity require full pixels.
Older producers such as Mutter 46 omit `SPA_DATA_FLAG_MAPPABLE` on MemFd.
When the pinned PipeWire client leaves that system-memory plane unmapped, capture
maps its bounded backing file read-only, retains the mapping for that buffer's
lifetime, and releases it on removal or stream closure. The producer keeps its
file descriptor; DMA-BUF remains outside this CPU capture contract.
Static refinements wait for both changed pixels and input to settle; an input
event during PNG encoding retires the candidate before transport admission.
Already admitted H.264 dependencies and binary packets remain ordered. This is
not DMA-BUF zero-copy, libei input or network congestion adaptation.
Video and refinement
`timestamp` values identify the latest changed source pixels on the host's
monotonic clock. They are telemetry, not a cross-host clock or decode ordering
key. Qualification must distinguish canvas drawing from a later animation-frame
receipt and must never report native input admission as painted response latency.
The decoder's static-frame drain deadline moves on both input and output progress;
a delayed browser callback cannot declare recently delivered output stalled.

Run the focused Python `host_desktop_*_test` modules, Go `TestHostDesktop` tests,
`node --test host_desktop_player.test.mjs` and `swift test` during development.
`HostDesktopSelfTest` proves synthetic video/audio encode-decode operation and
mandatory PipeWire client-module linkage in an installed Linux component. The
private loader must resolve both module paths and their soname dependencies.
The check also starts the real helper with an invalid command and verifies
clean signal shutdown while its command pipe remains open. It deliberately
does not request screen authorization.
`qualification/host_desktop_synthetic.py` measures the production encoder using a
synthetic source; receipt FPS is not client-painted FPS. The macOS qualification
executable's `probe` command is read-only; `session` uses the public native engine.
`window-streams` captures only its own application window (JPEG and H.264) alongside
the authorized physical display, proving independent generations and first-frame
admission with one helper process. The input qualifier's `--separate-session`
keeps the task editor and input sender in different processes; `--text-mode paste`
checks clipboard mutation, held-key rejection, recovery and retired authority.
`--compose-first` completes a real host IME composition before switching to client
text. It does not certify simultaneous unfinished host/client compositions.
Physical desktop, OS consent, input, reconnect and office-performance acceptance
require dedicated real-host evidence. No synthetic test certifies those claims.
`qualification/host_desktop_pipewire.py` runs the installed client against a
private synthetic PipeWire daemon on both native Linux architectures. It checks
control/view/control transitions, retained portal-FD reuse, metadata negotiation,
cursor-free control pixels and composited view pixels with both library-mapped
buffers and producer-allocated MemFd lacking MAPPABLE. The latter uses Mutter 46's
384-pixel cursor metadata allocation and verifies mapping cleanup, without requesting OS
screen sharing or reading any user desktop. Weak-VM performance is observational,
not a functional or distribution release threshold.
Wayland capture copies producer-owned pixels directly into a bounded Gst buffer
pool (four buffers with a separate cursor, six with an embedded cursor). The
retained clean frame is the comparison baseline; it is never painted with a
cursor. Native memmove/pixman work releases the Python interpreter lock and
preserves BGRA/BGRx/RGBA/RGBx, padded rows, and legacy MemFd mapping. Encoder
views retain the parent buffer lease until release. Pool exhaustion waits on the
capture thread without losing the final changed frame; shutdown flushes the pool
before joining that thread. `qualification/host_desktop_pixels.py` exercises
these lifetime, cancellation, pixel-format and cursor contracts with the
installed native stack on both Linux architectures. It does not access a desktop.
`qualification/host_desktop_portal_input.py` uses a private synthetic D-Bus service
that withholds replies until every ordered input and release arrives. It proves
nonblocking submission with the installed Gio binding without injecting OS input.

To refresh the media catalog, run `scripts/host_desktop_catalog.py` with explicit
private `--work` and candidate `--output` paths. Verify all acquired APK signatures
against the publisher's architecture-specific keys before committing the candidate.
Review its package identities, dependency closure, license/source metadata and
sizes, then qualify fresh installations on both native architectures. A changed
helper or wrapper is bound into the component preparation digest. Never mutate a
published component, module version or tag to repair a changed implementation.
`host_desktop_releases.json` retains reviewed helper hashes for published
installation identities. An upgrade verifies the old installation without
rewriting it and activates the new digest only after its own installed check.
Public `ResolveHostDesktopTools` still accepts only the current helper contract;
an older installation remains identifiable without becoming new session code.

The portable media stack retains its isolated musl ABI. NVIDIA encoding uses a
separate, verified `desktop-nvenc` worker built natively for amd64/arm64 against
glibc 2.31. It loads only the installed system CUDA/NVENC driver through its
standard loader; no NVIDIA binaries, CUDA toolkit or host media tools are
installed. The pinned nv-codec-headers 12.1 API requires a compatible driver
(Linux minimum 530.41.03); runtime version and synthetic encode checks determine
availability. A failed pre-session probe selects the existing explicit software
candidate. An active NVENC failure suspends media and never silently changes the
reference chain. Actual session dimensions must also encode successfully.

`native/host-desktop/Dockerfile` and `scripts/build_host_desktop_nvenc.sh` own the
native build. Pinned Debian images and signed snapshot archives establish the
compiler baseline. The committed manifest records source/header hashes, ELF
architecture, system dependencies and GLIBC versions. Binary, manifest and full
licensed headers enter the existing host-desktop preparation identity; they do
not enter the private-application desktop package. Installed workers inherit only
private pipes and a minimal environment. They cannot discover a display or inject
input. One request is outstanding, with bounded lengths, sequence validation and
a three-second deadline; cancellation kills/reaps only that owned child. Blocking
GPU work runs off the GLib input dispatcher. Four preallocated NVENC resources
satisfy the SDK allocation requirement without queuing four pictures.

NVENC uses synchronous Linux completion, P1 ultra-low-latency tuning, no B frames
or lookahead and one-frame CBR VBV. X11 readback, scaling and raw-pixel IPC remain
CPU-visible; this is not a zero-copy capture claim. Record the actual encoder,
resolution, capture backend and measured client response. GPU availability or
synthetic encoder throughput alone cannot establish desktop latency.

## Privileged physical desktop deployment

The `hostdesktop` package owns the optional Linux service. Its command imports
only that protocol/service package; it must not import the distribution root and
recursively embed its own executable. `scripts/build_host_desktop_service.sh`
builds static amd64/arm64 commands and records every production source hash.

The DRM worker is original glue around the separately licensed MIT `libdrmtap`
dependency pinned by `native/host-desktop/drm/source.json`. No RustDesk application
code is copied. Build its reviewed pristine checkout read-only in the signed,
pinned disposable builder using `scripts/build_host_desktop_drm.sh`. The build
disables the dependency's privilege helper and records original sources, MIT
license, signed builder packages, compiler, ELF requirements and final bytes.
Only the worker and license are redistributed; host libdrm/EGL/GPU drivers remain
system dependencies. Rebuild manifests after changing any recorded source.

`LoginScreenServiceKit` extracts reviewed release bytes without installation.
The consumer's Env App SSH adapter confirms root service scope, verifies bytes in
a private root directory before execution, and runs `manage`. Its JSON policy
contains no credentials. Wait for root authority before sending that policy;
otherwise an unused sudo password could become service input. Keep management
stdin open through completion, close it on cancellation, and wait for reported
rollback before terminating SSH. Failed or unknown rollback is a visible failure.

Native qualifiers refuse an existing service installation. With explicit root
authorization, `FLOE_LOGIN_DEPLOYMENT_KIT` runs real systemd install/cancel/start/
stop/update/uninstall and unprivileged attachment tests. `FLOE_LOGIN_SERVICE_WORKER`
qualifies capture/input teardown independently. A disconnected display qualifier
uses `FLOE_LOGIN_EXPECT_DISCONNECTED=1` and must report missing scanout rather than
claim success for capture or unlock. These checks do not replace real wrong-
password/successful-unlock, user-switch and product Viewer qualification.

`FLOE_LOGIN_QUALIFY_UNLOCK=1` additionally starts with a sleeping display, locks
the current GNOME session, verifies an invalid physical credential stays locked,
and requires the operator's credential through ephemeral stdin for actual unlock.
It rejects old-generation input and successor input before paint. Never supply
the credential in an environment variable, command argument, log or file. Disable
terminal echo when using an interactive qualification pipe. The qualifier does
not reset credentials or call an OS unlock API.

`FLOE_LOGIN_QUALIFY_SWITCH=1` creates a real GDM transient greeter, verifies its
physical frame and input teardown, and reactivates the original session without
unlocking it. All deployment qualifiers also keep a real acknowledged key down
while stopping the service or killing the Runtime fixture and verify the kernel
devices disappear before a new attachment. These opt-in fixtures must never run
in ordinary source CI. Physical scanout capture uses an unprivileged converter
and the shared H.264 media scheduler, followed by one lossless PNG refinement
after pixels settle. Independent hardware-cursor updates carry position, explicit
visibility and hotspot validity. Clipboard and audio are not advertised by this
service. Existing current-user desktop transports retain their independent media
capabilities.
