These two Alpine APK files are test-only archives needed to qualify upgrades from
the published v0.22.29 catalog. Alpine retired the exact 1.6.58-r1 download URLs;
the current catalog uses publisher-signed 1.6.59-r0 from the original CDN.
No production catalog or install path reads these fixtures.

Both archives retain the original Alpine publisher signatures and are pinned by
SHA-256 in SHA256SUMS. Their package metadata identifies the license as Libpng.
Source catalog: https://github.com/floegence/floe-native-apps/blob/v0.22.29/catalog.json
Original amd64 source: https://dl-cdn.alpinelinux.org/alpine/v3.23/main/x86_64/libpng-1.6.58-r1.apk
Original arm64 source: https://dl-cdn.alpinelinux.org/alpine/v3.23/main/aarch64/libpng-1.6.58-r1.apk
