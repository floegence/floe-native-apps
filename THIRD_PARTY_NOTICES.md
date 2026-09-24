# Third-party notices

The native catalog downloads original publisher archives. Their URLs, hashes,
sizes, source locations and licenses remain in `catalog.json`; original license
files are retained during extraction. No aggregate third-party binary stack is
redistributed by this module.

## Xpra HTML client fixtures and preparation

`testdata/input/*.gz` contains unmodified source files from
[Xpra HTML5 v20](https://github.com/Xpra-org/xpra-html5/tree/v20/html5) and
[v21](https://github.com/Xpra-org/xpra-html5/tree/v21/html5), compressed only to
reduce fixture size. These files retain their original copyright notices and
are licensed under the [Mozilla Public License 2.0](licenses/xpra-html5-MPL-2.0.txt).
`Client-v20.js`, `Window-v20.js`, `index-v20.html`, `Keycodes-v20.js` and `Protocol.js` come from
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
