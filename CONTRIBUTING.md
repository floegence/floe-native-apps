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
Consumers select a policy after connection startup; `false` means the server did
not negotiate the display contract. Every connection starts at logical density.
Native density uses integral ceil DPR, bounded to 1 through 4 and to the server's
advertised maximum desktop dimensions. Resizing or moving between display densities
recomputes the backing resolution without reconnecting. The private display's DPI
and GTK scale change together; logical window geometry, dialog headers, cursor
hotspots and pointer targets remain stable. Applications retain their own support
or limitations for live DPI changes. No host desktop settings are modified.

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
native density is not a guarantee of low latency during continuous motion. Existing
applications retain their prepared assets until they exit and are launched again.

Source tests execute both prepared clients, including DPR changes, server size
bounds, shadow cursor geometry and disconnect disposal. Native release qualification
runs `TestNativeClientInput` at density 1 and `TestNativeDisplayInput` at density 2
with managed and system Xpra on both architectures. GTK receipts also assert its
actual backing scale and unchanged logical text DPI. Both runs retain the complete
Unicode, focus and clipboard assertions below.

### Cursor and input

`PrepareInputClient` also prepares one connection-owned cursor path for reviewed
HTML v20/v21. Remote PNGs preserve their shape and alpha, with a maximum longest
edge of 24 CSS pixels and no enlargement of smaller images. Hotspots scale with
the image. Integral backing density (ceil DPR, bounded to 1 through 4) is declared
through CSS image-set; it changes resolution, never logical geometry. Malformed
metadata, images over 1024 pixels per edge or 4 MiB encoded, and failed decodes
reset to the system cursor. Reset, disconnect and newer packets revoke unfinished
decodes. Existing application instances retain their prepared resources; consumers
must not rewrite a live application's assets to upgrade its cursor.

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
