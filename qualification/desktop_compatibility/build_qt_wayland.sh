#!/bin/sh
# Compile in the disposable native qualification system, never on a user host.
set -eu
source_dir=$1
output=$2
mkdir -p "$output/platforminputcontexts"
version=$(pkg-config --modversion Qt6Gui)
includes=$(pkg-config --variable=includedir Qt6Gui)
moc=$(pkg-config --variable=libexecdir Qt6Core)/moc
"$moc" $(pkg-config --cflags Qt6Gui Qt6DBus) \
    -I"$includes/QtGui/$version/QtGui" "$source_dir/qt_wayland.cpp" -o "$output/qt_wayland.moc"
c++ -shared -fPIC -Wall -Wextra -Werror -O2 -std=c++17 \
    -I"$output" -I"$includes/QtGui/$version" -I"$includes/QtGui/$version/QtGui" \
    -I"$includes/QtCore/$version" -I"$includes/QtCore/$version/QtCore" \
    $(pkg-config --cflags Qt6Gui Qt6DBus wayland-client) "$source_dir/qt_wayland.cpp" \
    $(pkg-config --libs Qt6Gui Qt6DBus wayland-client) \
    -o "$output/platforminputcontexts/libfloe-client-wayland.so"
