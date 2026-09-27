#!/bin/sh
# Run on the target Linux architecture. All application processes belong to the
# temporary fixture; Docker is used only for clean distribution qualification.
set -eu
: "${NATIVE_CHECK:?Set NATIVE_CHECK to the absolute qualification executable}"
: "${NATIVE_ROOT:?Set NATIVE_ROOT to the absolute installed component root}"
recipe=${NATIVE_RECIPE:-xpra}
case "$recipe" in xpra|desktop) ;; *) echo "Unknown native recipe: $recipe" >&2; exit 1;; esac
"$NATIVE_CHECK" -recipe "$recipe" -check "$NATIVE_ROOT"
# Pull distribution fixtures from their canonical publishers. The shared ECR
# mirror can exhaust its data quota independently of native qualification.
images="docker.io/library/debian:13-slim docker.io/library/alpine:3.23"
if [ "${NATIVE_ARCH:-$(uname -m)}" = amd64 ] || [ "${NATIVE_ARCH:-$(uname -m)}" = x86_64 ]; then
  images="$images docker.io/library/archlinux:base docker.io/library/rockylinux:9 docker.io/library/almalinux:9 registry.access.redhat.com/ubi9/ubi-minimal:latest"
fi
for image in $images; do
  echo "Qualifying $image"
  # Public registries may throttle an anonymous runner. Retry acquisition only;
  # a native qualification failure must remain a failure on its first attempt.
  delay=10
  until docker pull "$image"; do
    if [ "$delay" -gt 40 ]; then
      echo "Image acquisition exhausted its retry budget: $image" >&2
      exit 1
    fi
    echo "Image acquisition failed; retrying in ${delay}s: $image" >&2
    sleep "$delay"
    delay=$((delay * 2))
  done
  docker image inspect --format '{{json .RepoDigests}}' "$image"
  docker run --rm --user nobody --env HOME=/tmp -v "$NATIVE_ROOT:/components:ro" -v "$NATIVE_CHECK:/check:ro" "$image" /check -recipe "$recipe" -check /components
done
