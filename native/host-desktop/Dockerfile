# Disposable build fixture only; no container or package installation at runtime.
ARG TARGET_ARCH
FROM public.ecr.aws/docker/library/debian@sha256:032c9cd584e6c6d7a98e7edb50ce9233b2c585f4f7e2a5cd093521af779886df AS amd64
FROM public.ecr.aws/docker/library/debian@sha256:131ddca8f135f432bbf51f9e7523d246634a68ccadb14469f1050f882c86f0a9 AS arm64
FROM ${TARGET_ARCH} AS builder
COPY ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
RUN rm -f /etc/apt/sources.list.d/* /etc/apt/apt.conf.d/docker-clean && \
    printf '%s\n' 'deb [check-valid-until=no] https://snapshot-cloudflare.debian.org/archive/debian/20250224T000000Z/ bullseye main' > /etc/apt/sources.list && \
    apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev binutils python3 && \
    sha256sum /var/lib/apt/lists/*InRelease /var/cache/apt/archives/*.deb > /builder-packages.sha256
WORKDIR /source
