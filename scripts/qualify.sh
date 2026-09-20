#!/bin/sh
# Run on the target Linux architecture. All application processes belong to the
# temporary fixture; Docker is used only for clean distribution qualification.
set -eu
: "${NATIVE_CHECK:?Set NATIVE_CHECK to the absolute qualification executable}"
: "${NATIVE_ROOT:?Set NATIVE_ROOT to the absolute installed component root}"
"$NATIVE_CHECK" -check "$NATIVE_ROOT"
images="public.ecr.aws/docker/library/debian:13-slim public.ecr.aws/docker/library/alpine:3.23"
if [ "${NATIVE_ARCH:-$(uname -m)}" = amd64 ] || [ "${NATIVE_ARCH:-$(uname -m)}" = x86_64 ]; then
  images="$images public.ecr.aws/docker/library/archlinux:base public.ecr.aws/docker/library/rockylinux:9 public.ecr.aws/docker/library/almalinux:9 registry.access.redhat.com/ubi9/ubi-minimal:latest"
fi
for image in $images; do
  echo "Qualifying $image"
  docker pull "$image"
  docker image inspect --format '{{json .RepoDigests}}' "$image"
  docker run --rm --user nobody --env HOME=/tmp -v "$NATIVE_ROOT:/components:ro" -v "$NATIVE_CHECK:/check:ro" "$image" /check -check /components
done
