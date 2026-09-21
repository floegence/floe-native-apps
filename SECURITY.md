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
not select catalogs, URLs, executables, hashes, or installation paths.

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
