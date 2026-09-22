# Native application support

This repository owns the reusable, optional native graphical support distribution:
its pinned upstream artifact catalog, bounded acquisition and extraction, native
tool environment, Linux launch/process-observation primitives, and isolated
graphical self-check. Consumers own authentication,
application inventory, application sessions, UI, and launch continuation.
The public artifactcache package also supplies pinned archive acquisition and
verification to trusted host catalogs. Consumers own those catalogs, consent and
cache placement; renderer input must never become an acquisition specification.

Applications run directly on their host. Never introduce a container, virtual
machine, global library path, system package installation, security-policy bypass,
or automatic privileged fallback into the production path. Containers may be used
as disposable distribution test fixtures. Components must not contaminate the
environment of the user's applications.

Use one dedicated feature worktree and branch. Keep main clean, preserve commits,
integrate by fast-forward, and remove only task-owned worktrees and branches.
Use English and Conventional Commit messages. Use published dependencies only.
Primary agents own implementation and tests; do not delegate routine work.

Before release, run Go race tests, vet, formatting, catalog integrity, malicious
archive cases, cancellation/restart tests, and native installed-stack checks.
Publish no platform claim without corresponding observable evidence. Keep source
CI short; actual graphical qualification belongs to explicit release validation.
An incomplete download, failed integrity check, or failed graphical self-check
must never activate a package. Preserve usable installed packages during repair.

The catalog pins upstream archive bytes and records their original license and
source metadata. Native archives are acquired from their original publishers;
this repository must not silently redistribute third-party binaries or replace
their license/source obligations with a generated aggregate notice.

Go 1.27.1 in go.mod is authoritative and must track Redeven's Go toolchain.
Use scripts/check.sh for the source gate, pinned go tool invocations for
vulnerability checks, and Release qualification for fresh native installation
and distribution evidence. Ordinary push/PR checks remain source-only.
Read CONTRIBUTING.md and SECURITY.md before changing release or trust boundaries.
Check both Git author and committer identity before every task's first commit;
never invent a GitHub noreply address. Published tags and module bytes are
immutable; correct historical display attribution through an accurate .mailmap.
