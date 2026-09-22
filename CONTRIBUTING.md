# Contributing

Floe Native Apps owns native component preparation. Keep application inventory,
product authorization, viewers, and application sessions in the consuming host.
Read [AGENTS.md](AGENTS.md) for the implementation and repository boundaries.

## Development

Use Go **1.27.1**, Python 3, Git, and a POSIX shell. The Go version in `go.mod`
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
