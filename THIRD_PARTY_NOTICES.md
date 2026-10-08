# Third-party notices

The native catalog downloads original publisher archives. Their URLs, hashes,
sizes, source locations and licenses remain in `catalog.json` and
`desktop_catalog.json`; original license files are retained during extraction.
Original runtime APKs are acquired from their publishers. The combined desktop
recipe additionally distributes the explicitly identified source derivations
below in `native/dist`, together with their corresponding source and notices.

## Image codecs

`qualification/host_desktop_pipewire_source.c` adapts PipeWire's MIT-licensed
`src/examples/video-src.c` by Wim Taymans. It is a synthetic release fixture,
not a distributed runtime component; its source retains the copyright/license
notices. The public source is available in the
[PipeWire repository](https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/1.4.9/src/examples/video-src.c).

The native capture helper dynamically uses the catalog's original libpng,
libjpeg-turbo, and libwebp packages. Their original BSD/zlib/libpng license and
source metadata are retained in `desktop_catalog.json`; no third-party codec
source is copied into the helper. The Go transport validates WebP headers using
`golang.org/x/image/webp` under the Go project's BSD 3-Clause license, as pinned in
`go.mod` and `go.sum`.

## Xpra HTML client fixtures and preparation

`testdata/input/*.gz` contains unmodified source files from
[Xpra HTML5 v20](https://github.com/Xpra-org/xpra-html5/tree/v20/html5) and
[v21](https://github.com/Xpra-org/xpra-html5/tree/v21/html5), compressed only to
reduce fixture size. These files retain their original copyright notices and
are licensed under the [Mozilla Public License 2.0](licenses/xpra-html5-MPL-2.0.txt).
`Client-v20.js`, `Window-v20.js`, `index-v20.html`, `Keycodes-v20.js`, `OffscreenDecodeWorker.js` and `Protocol.js` come from
v20; files with `v21` in their names come from v21.

`PrepareInputClient` modifies a caller's installed Xpra distribution locally.
Prepared Xpra files retain the publisher's notices and MPL license. The adapter
in `input_client.js`, the cursor owner in `cursor.js`, and the preparation
implementation are first-party source. The unmodified `Window.js` fixture from
both tags has SHA-256 `8381c5b10502bf738cfb1c31a9f68cbe84db89aa644667e8095eacd57fe2d65d`.

## XIM protocol library and toolkit adapters

The r3 catalog adds the original Alpine xcb-imdkit 1.0.9 (LGPL-2.1-only) and
xcb-util 0.4.1 (MIT) packages. `input_xim.py` calls their public ABI; it does not
copy or reimplement the XIM protocol. Those libraries run only in the private
support process and are not exported as application loader paths.

`input_modules/dist` contains first-party MIT-licensed GTK3/GTK4 and Qt5/Qt6 input
adapters built from `input_modules/gtk3.c`, `gtk4.c`, `gtk_commit.h` and `qt.cpp`. They link
dynamically to the application's installed GTK/GLib or Qt libraries. This module
does not redistribute GTK, GLib, Qt or glibc with those adapters. Their normal
licenses and source obligations remain with the corresponding library
distributors. Per-architecture build records retain the pinned Debian image,
snapshot, compiler/package versions, source hashes and binary hashes.

`native/qt_native.cpp` is the first-party confirmed-text adapter for
the combined Wayland/Xwayland backend. Its native build fixtures use the same
Debian Qt ABI baselines, with original Wayland client headers and libraries from
the signed snapshot. The adapter dynamically links the application's Qt and
Wayland libraries. `prepare_qt_baseline.py` copies original runtime libraries only
inside disposable native test resources to verify those exact baseline versions;
those libraries are not redistributed with the adapter. The two dynamically
linked adapter binaries and their build records are in `native/dist/<arch>`.

`native/gtk_native.c` is the corresponding first-party MIT-licensed GTK3/GTK4
adapter for the combined backend. It dynamically resolves the application's
GTK/GLib libraries. `native/dist/<arch>/gtk` contains only these two adapters;
`provenance/gtk.json` records the native Debian 11 build, signed package archives,
original toolkit source hashes and ELF dependencies. The GTK/Pango sources below
are unmodified build inputs, not bundled application runtimes.

The GTK4 build fixture compiles the original GTK 4.0.3 source archive from
https://download.gnome.org/sources/gtk/4.0/gtk-4.0.3.tar.xz (SHA-256
`d7c9893725790b50bd9a3bb278856d9d543b44b6b9b951d7b60e7bdecc131890`), under
LGPL-2.1-or-later. The fixture also builds original Pango 1.48.10 under
LGPL-2.1-or-later; `scripts/input_toolkits.json` pins both original archive URLs
and SHA-256 values. GTK and Pango remain inside the disposable build/test fixture; only the
first-party dynamically linked adapter is distributed. Build records identify
that original source and do not change the native component catalog.

The focus browser fixture uses npm-published jQuery 3.7.1 and jQuery UI 1.13.3
(MIT), pinned with package integrity in `qualification/package-lock.json`. It
executes the original reviewed Xpra window constructors and their real jQuery UI
listeners, including decoration drag, together with the published controllers.

## Combined desktop native distribution

`native/patches/weston-14-input-events.patch` is a reviewed modification of
Weston 14.0.2 source, licensed under [the original Weston notices](licenses/weston-14-COPYING.txt).
The adjacent JSON records the original archive, original source hashes and patch
hash. `native/dist/<arch>/artifacts/libweston-14.so.0`, `xwayland.so` and
`headless-backend.so` are derived from that source. The headless modification
resizes the software framebuffer together with native output coordinates.
The first-party shell and capture client link to the
original public/private ABI of the pinned version. Original publisher archives
and already activated installations are not modified. Build records identify the
compiler, signed packages, patches, build scripts and final binary hashes.

The complete original Weston 14.0.2 source archive is retained in
`native/dist/common/sources/weston-14.0.2.tar.xz`; the reviewed patch and build
scripts are in `native/dist/common/sources/tree`. The archive preserves the
individual source notices, including the capture protocol used for generated
client code. The original Wayland protocols 1.46 `text-input-unstable-v3.xml`
is also retained there under `native/protocols`, including its Intel, Jan Arne
Petersen, Red Hat and Purism copyright and permissive license. It matches the
original signed Alpine `wayland-protocols-1.46-r0` build dependency (SHA-256
`2d08f2cddb463e169c23f1c34769de12ae255540e51ee8f515b54667d60b90ba`).

## IBus context provenance derivation

`native/patches/ibus-1.5-context-source.patch` derives a read-only context
provenance interface from IBus 1.5.33, under its original
[LGPL-2.1-or-later license](licenses/ibus-COPYING.txt). The adjacent JSON records
the original publisher archive and source hashes. The private daemon reports
the actual context connection; its official portal reports the original
session-bus owner. Neither interface exposes text or performs input or process
operations. The derived `ibus-daemon`, `ibus-portal` and `libibus-1.0.so.5`
are distributed in `native/dist/<arch>/artifacts`, under LGPL-2.1-or-later.
The full original release source, including all notices, is distributed as
`native/dist/common/sources/ibus-1.5.33.tar.gz`. Its exact modification and
complete build scripts are in the accompanying `sources/tree`; the license is
also available in `native/dist/common/licenses/ibus-COPYING.txt`. Build records
retain source, patch, compiler, dependency and final artifact identities.

IBus executables dynamically link their accompanying IBus library and original
GLib/D-Bus libraries. The first-party Go/Python host invokes them as separate
processes; it does not statically link IBus. You may modify and rebuild the
corresponding source and relink/replace the library under its license. The
published package verifies its reviewed bytes; to distribute a modified build,
regenerate its manifest/catalog and build the host against that modified source
package, rather than changing a running application's immutable files. See
the corresponding-source build instructions in `CONTRIBUTING.md`. No license
permission is narrowed by the integrity checks, and reverse engineering for
debugging such library modifications is not prohibited by this project.

Complete corresponding source accompanies the binary files at the same module
location; this distribution does not rely on a future source offer. The original
runtime APKs retain their own publisher-provided source and license metadata.


## NVIDIA video encoding API headers

`native/host-desktop/vendor/nvEncodeAPI.h` and `dynlink_cuda.h` are unchanged
headers from FFmpeg/nv-codec-headers tag `n12.1.14.0`:
https://github.com/FFmpeg/nv-codec-headers/tree/n12.1.14.0/include/ffnvcodec

Their NVIDIA and contributor copyright notices and permissive MIT-style license
terms are retained in full in each file and included in the prepared component.
The native build manifests pin their exact SHA-256 values. No NVIDIA driver or
CUDA toolkit binaries are distributed; a compatible user-installed system driver
is required for this optional encoding capability.


## libdrmtap physical scanout API

The optional `desktop-drm` worker statically links the MIT-licensed libdrmtap
0.5.8 source at [the pinned upstream commit](https://github.com/rustdesk-org/libdrmtap/tree/95d4d74549631aa5c39461300acfd2e106583cc9).
Its unchanged copyright and full MIT permission notice accompany both architecture
artifacts as `native/host-desktop/drm/dist/<arch>/libdrmtap.LICENSE`, and accompany
the installed system service. `source.json` and each build manifest record the
original publisher, commit, individual source hashes and build configuration.
The dependency's privilege helper is disabled; the first-party worker, service,
protocol and SSH lifecycle are independently implemented. Host libdrm/EGL and
GPU vendor drivers are dynamically loaded system dependencies, not redistributed.
