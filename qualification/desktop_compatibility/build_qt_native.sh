#!/bin/sh
# Compile in the disposable native qualification system, never on a user host.
set -eu
source_dir=$1
output=$2
major=${3:-6}
case "$major" in 5|6) ;; *) exit 1 ;; esac
mkdir -p "$output/platforminputcontexts"
version=$(pkg-config --modversion "Qt${major}Gui")
includes=$(pkg-config --variable=includedir "Qt${major}Gui")
if [ "$major" = 5 ]; then
    moc=$(pkg-config --variable=host_bins Qt5Core)/moc
else
    moc=$(pkg-config --variable=libexecdir Qt6Core)/moc
fi
"$moc" $(pkg-config --cflags "Qt${major}Gui" "Qt${major}DBus") \
    -I"$includes/QtGui/$version/QtGui" "$source_dir/qt_native.cpp" -o "$output/qt_native.moc"
c++ -shared -fPIC -Wall -Wextra -Werror -O2 -std=c++17 \
    -I"$output" -I"$includes/QtGui/$version" -I"$includes/QtGui/$version/QtGui" \
    -I"$includes/QtCore/$version" -I"$includes/QtCore/$version/QtCore" \
    $(pkg-config --cflags "Qt${major}Gui" "Qt${major}DBus" wayland-client) "$source_dir/qt_native.cpp" \
    $(pkg-config --libs "Qt${major}Gui" "Qt${major}DBus" wayland-client) \
    -o "$output/platforminputcontexts/libfloe-client-native.so"
