#!/bin/sh
# Compile only in the disposable native qualification VM.
set -eu
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
output=$1
mkdir -p "$output/platforminputcontexts"
version=$(pkg-config --modversion Qt6Gui)
includes=$(pkg-config --variable=includedir Qt6Gui)
moc=$(pkg-config --variable=libexecdir Qt6Core)/moc
"$moc" $(pkg-config --cflags Qt6Gui Qt6DBus) \
    -I"$includes/QtGui/$version/QtGui" "$source_dir/qt_probe.cpp" -o "$output/qt_probe.moc"
c++ -shared -fPIC -Wall -Wextra -Werror -O2 -std=c++17 \
    -I"$output" -I"$includes/QtGui/$version" -I"$includes/QtGui/$version/QtGui" \
    -I"$includes/QtCore/$version" -I"$includes/QtCore/$version/QtCore" \
    $(pkg-config --cflags Qt6Gui Qt6DBus) "$source_dir/qt_probe.cpp" \
    $(pkg-config --libs Qt6Gui Qt6DBus) \
    -o "$output/platforminputcontexts/libfloe-prototype.so"
