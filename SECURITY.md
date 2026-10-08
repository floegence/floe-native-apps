# Security policy

## Report privately

Use [GitHub private vulnerability reporting](https://github.com/floegence/floe-native-apps/security/advisories/new).
Do not disclose exploit details, credentials, private file paths, application
content, or archive payloads in a public issue. Include the affected tag or
commit, host architecture/distribution, impact, and a minimal reproduction.
If the reporting form is unavailable, open an issue asking only for a private
contact channel. Maintainers will coordinate remediation and disclosure there.

## Supported versions

Security fixes target the latest released version. Upgrade consumers to a new
immutable tag; previously published tags are never rewritten. There is currently
no long-term support branch or guaranteed response SLA.

## Security boundary

The SDK trusts its compiled, reviewed artifact catalog and its product-owned
private state directory. It verifies original archive sizes and SHA-256 hashes,
contains extraction paths/links and expansion, strips privileged file modes,
and activates only a complete stack after a graphical self-check. Clients must
not select catalogs, URLs, executables, acquisition specifications, or installation
paths. A transfer plan may carry component and archive identities only as
selectors within the matching compiled catalog; unknown identities, duplicate
entries and inconsistent totals are rejected. The receiving host independently
verifies the union of cached and transferred original bytes.

The public `artifactcache` package additionally accepts a trusted host's pinned
archive specification. That API does not authenticate or approve specifications:
the host resolves them from reviewed source, never renderer or client input.
It verifies HTTPS acquisition, exact length and SHA-256 before publishing into
the host's private cache. It neither extracts nor executes the archive.

The host product owns authentication, authorization, consent, state-directory
permissions, network/session exposure, and application lifecycle. The SDK's
owner identifiers bind admitted operations to callers; they do not authenticate
a request. Local code running as the same user or an administrator can modify
that user's state and is outside the private-directory trust boundary.

Client resource snapshots read only recognized static files from a prepared
private HTML directory, reject asset symlinks and bound file count/size. Their
digest covers the actual transformed bytes. Documents, configuration and session
data are excluded. A host must authorize requests before selecting the exact
resource digest and must keep session documents and control traffic authenticated
and uncached, including after a viewer reuses cached scripts. The SDK never
installs certificate trust or bypasses browser security to enable video decoding.

`PreparedViewer` binds the prepared entry document to those exact static bytes.
The host pins one snapshot per share; preparing a new share must not rewrite a
running backend, loaded input modules, or application-owned instance files.
Capability errors disable only the affected operation. Unknown input protocols
remain denied; an incompatible module is reported only after authenticated
process registration. Missing input focus is not evidence of a module version.

Prepared tools and user applications run with host user permissions. This is
not an application sandbox. Applications must receive their original library
and interpreter environment, with only the private session's display and bus
addresses retained. Host execution policies remain authoritative; the SDK does
not install system packages, escalate privileges, or disable those policies.

## Automated checks and their limits

- CodeQL analyzes Go, Python, and GitHub Actions with extended security queries.
- `govulncheck` checks reachable vulnerabilities in Go runtime code and maintained
  development tools. Dependabot monitors Go and GitHub Actions dependencies.
- GitHub secret scanning and push protection detect supported secret patterns.
- Source tests cover malformed archives, boundary escape attempts, integrity,
  ownership, cancellation, interruption, and atomic activation. Release
  qualification exercises the installed native graphics stack.

These checks do not certify third-party native components as vulnerability-free.
The pinned Alpine packages, Xpra, and its HTML client require their own advisory
review and catalog updates. Report a vulnerable pinned component here with its
catalog identity and upstream advisory; do not assume Go dependency scanning
covers APK archives. No scanner replaces review of changes to the trust boundary.

## Administrator-authorized physical desktop service

`hostdesktop` is a separate, opt-in root service boundary. The consuming product
must confirm the installation scope and collect any administrator credential in
its SSH UI. The SDK never requests sudo, installs on startup, receives a password,
or sends credentials to the daemon. The management command receives a bounded
policy and a liveness stream; cancellation or loss of that stream rolls back the
unit, policy and activation state. Its progress contains fixed stages/error codes.

The root-owned systemd service listens only on a private Unix socket. It admits
one administrator-pinned Runtime image and UID, checks peer PID/start-time/image,
and consumes a single-use five-second attachment ticket. Connection loss, image
change, seat/session change, stop and generation changes release all held input.
A lock frame can be visible without ordinary input authority. Unlock accepts
physical key/pointer events tied to the painted frame and current generation;
clipboard, text paste, audio and shortcuts are unavailable on that chain. No
service log contains input content. Disk encryption is outside this boundary.

DRM export and GPU conversion have separate processes and inherited private
sockets. Only the exporter retains device/DRM authority. EGL/GLES conversion runs
as the Runtime user after clearing Linux capabilities; no GPU vendor driver is
loaded into the root exporter. The service does not change device ACLs, host driver
libraries, compositor permissions, or user security policies. Current qualification
covers GNOME/GDM; other lockers must not be inferred from a PAM session label.
No scanout means no capture capability, even when the service itself is active.

User home directories stay hidden in the service namespace. Only `/run/user` is
bound read-only for the qualified compositor's session bus. A fixed child drops
UID and every Linux capability before using Mutter's display-power property.
It never unlocks the session or sends pre-frame input, changes desktop consent,
or overrides unsupported display power. Input still requires successor paint.
