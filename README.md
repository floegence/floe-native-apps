# Floe Native Apps

Prepare a private native graphical stack for Linux applications. Applications
continue running directly on the host. The library downloads pinned original
archives, verifies every byte, extracts into a private staging directory, and
activates only after an isolated Xpra session proves GIO application launch, window discovery, decoded
pixels, and input delivery.

The host product owns consent, authorization, application metadata and launch,
UI, and window lifecycle. This library owns only component preparation.

## Use

```go
pkg, err := nativeapps.NativePackage()
// Handle unsupported platforms before offering preparation.
manager, err := nativeapps.New(absolutePrivateStateRoot, pkg, nil)
defer manager.Close()
status, err := manager.Start(authenticatedOwner, requestID, "download", 0)
```

Watch notifications are coalesced invalidations: read `Snapshot` after subscribing
and after each notification. A disconnected observer does not cancel preparation.
Retain the same request ID when response delivery is uncertain. Cancellation
requires the original authenticated owner. A restart reports interrupted work;
it never silently restarts a download or executes partial files.

After `ready`, resolve `Directory` and `ResolveTools`. Use the complete managed
toolset and `Tools.Environment` for support processes. Before launching a user
application, restore the saved `FLOE_NATIVE_APPLICATION_ENV` map onto its launch
context and remove both `FLOE_NATIVE_APPLICATION_ENV` and `FLOE_NATIVE_ROOT`.
Keep the session's display and D-Bus addresses. Never export the private loader
path to the host program.

## Components and portability

The checked-in catalog pins Alpine 3.23 Xpra 6.2.2 packages and Xpra HTML5 v20
source. Each architecture carries its own musl loader and library closure; it
does not use the host's glibc or musl installation. Supported architectures are
Linux amd64 and arm64. macOS capture and permissions belong to the consuming
product's native macOS integration.

No root password, package manager, desktop environment, physical monitor,
container, or virtual machine is required in production. A writable executable
state filesystem, a compatible Linux kernel, ordinary process/Unix socket
permissions, and network access to the pinned publishers are required. Host
security policy remains authoritative; failure never enables a privileged
fallback. Individual applications still require their own host dependencies and
X11 support. Native Wayland-only applications and GPU/DRM capture are outside
this package's contract.

Xvfb embeds its xkbcomp directory. Installation verifies the reviewed binary
shape and replaces the single `/usr/bin` directory literal with `.` while its
private wrapper selects the package's tool directory. XKB data, D-Bus config,
fonts, Python and image loaders also use private paths. No host paths are linked
or changed. The graphical self-check exercises this relocation after installation.

## Offline preparation

```sh
go run ./cmd/floe-native-apps -arch arm64 -state /absolute/private/cache -bundle /absolute/native-arm64.zip
```

The ZIP contains original pinned publisher archives. Use its actual byte length
with `Start(owner, requestID, "upload", size)`, then `WriteChunk` with ordered
256 KiB maximum chunks and `CompleteUpload`. Replayed identical chunks are
idempotent. The receiving host independently verifies every archive and performs
the same native self-check. Never accept client-supplied executable paths, URLs,
catalogs, hashes, or package identities.

## Qualification and licenses

`go test -race ./...` and `go vet ./...` cover extraction boundaries, integrity,
owner/idempotency rules, interruption, cancellation, persistence failures, and
atomic activation. `floe-native-apps -state ...` performs actual installation;
`floe-native-apps -check ...` repeats the native picture/input qualification in
an installed directory. Disposable containers may test clean distributions;
they are never part of the production implementation.

This library is MIT-licensed. Catalog entries record original licenses and
source locations. Downloaded components retain their original licenses. This
repository publishes the installer and catalog, not a combined third-party
binary distribution. Redistributing an offline ZIP or installed stack creates
separate obligations under the components' licenses, including applicable source
availability obligations. Original license files are retained during extraction.

The v0.1.1 qualification exercised Ubuntu 22.04 arm64 and Debian 11 amd64
hosts, and clean Debian 13 and Alpine 3.23 fixtures on both architectures.
The same amd64 stack passed Arch Linux, Rocky Linux 9, AlmaLinux 9 and RHEL UBI 9 fixtures.
This evidence covers component rendering/input; it does not promise that every
third-party application works on every distribution.
