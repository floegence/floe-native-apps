# Native application support

This repository owns the reusable, optional native graphical support distribution:
its pinned upstream artifact catalog, bounded acquisition and extraction, native
tool environment, and isolated graphical self-check. Consumers own authentication,
application inventory, application sessions, UI, and launch continuation.

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
