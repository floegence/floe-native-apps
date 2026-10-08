# Disposable build fixture only; never installs host system packages.
ARG TARGET_ARCH=arm64
FROM public.ecr.aws/docker/library/debian@sha256:032c9cd584e6c6d7a98e7edb50ce9233b2c585f4f7e2a5cd093521af779886df AS amd64
FROM public.ecr.aws/docker/library/debian@sha256:131ddca8f135f432bbf51f9e7523d246634a68ccadb14469f1050f882c86f0a9 AS arm64
FROM ${TARGET_ARCH} AS builder
COPY ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
# The base includes a newer perl-base than the signed snapshot; select the
# snapshot's matching version only in this disposable builder.
RUN rm -f /etc/apt/sources.list.d/* /etc/apt/apt.conf.d/docker-clean && printf '%s\n' 'deb [check-valid-until=no] https://snapshot-cloudflare.debian.org/archive/debian/20250224T000000Z/ bullseye main' > /etc/apt/sources.list && apt-get update && apt-get install -y --allow-downgrades --no-install-recommends perl-base=5.32.1-4+deb11u3 gcc libc6-dev binutils python3 git libdrm-dev libegl-dev libgles-dev && sha256sum /var/lib/apt/lists/*InRelease /var/cache/apt/archives/*.deb > /builder-packages.sha256
WORKDIR /source
