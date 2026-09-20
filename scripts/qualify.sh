#!/bin/sh
# Run on the target Linux architecture. All application processes belong to the
# temporary fixture; Docker is used only for clean distribution qualification.
set -eu
: "${NATIVE_CHECK:?Set NATIVE_CHECK to the absolute qualification executable}"
: "${NATIVE_ROOT:?Set NATIVE_ROOT to the absolute installed component root}"
"$NATIVE_CHECK" -check "$NATIVE_ROOT"
for image in public.ecr.aws/docker/library/debian:13-slim public.ecr.aws/docker/library/alpine:3.23; do
  docker run --rm --user 1000:1000 -v "$NATIVE_ROOT:/components:ro" -v "$NATIVE_CHECK:/check:ro" "$image" /check -check /components
done
