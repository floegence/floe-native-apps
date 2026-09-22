package nativeapps

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

type Tools struct{ Root, Xpra, Python, Xvfb, Xauth, DBus, HTML string }

// XpraNoAudioArgs disables audio support for a silent Xpra 6.x graphics session.
// Muting the speaker and microphone with "off" still initializes audio codecs;
// a missing audio backend can then delay every server inventory request.
// Session ownership, transport, and all other feature choices remain with the host.
func XpraNoAudioArgs() []string {
	return []string{"--audio=no", "--pulseaudio=no", "--speaker=disabled", "--microphone=disabled"}
}

func ResolveTools(root string) (Tools, error) {
	if !filepath.IsAbs(root) {
		return Tools{}, ErrInvalid
	}
	bin := filepath.Join(root, "floe", "bin")
	t := Tools{root, filepath.Join(bin, "xpra"), filepath.Join(bin, "python3"), filepath.Join(bin, "Xvfb"), filepath.Join(bin, "xauth"), filepath.Join(bin, "dbus-run-session"), filepath.Join(root, "usr", "share", "xpra", "www")}
	for _, name := range []string{t.Xpra, t.Python, t.Xvfb, t.Xauth, t.DBus, filepath.Join(bin, "dbus-daemon"), filepath.Join(bin, "xkbcomp"), filepath.Join(bin, "gio-launch-desktop")} {
		info, err := os.Stat(name)
		if err != nil || !info.Mode().IsRegular() || info.Mode()&0111 == 0 {
			return Tools{}, fmt.Errorf("native executable unavailable: %s", filepath.Base(name))
		}
	}
	for _, name := range []string{"index.html", "js/Client.js", "js/Window.js", "js/Utilities.js"} {
		if info, err := os.Stat(filepath.Join(t.HTML, name)); err != nil || !info.Mode().IsRegular() || info.Size() == 0 {
			return Tools{}, errors.New("native HTML client unavailable")
		}
	}
	return t, nil
}

// Environment scopes support-tool overrides. The GIO launcher must restore this
// small saved map on its AppLaunchContext before starting the user's program.
// Display and D-Bus addresses are deliberately not restored: those belong to the
// caller's private graphical session.
func (t Tools) Environment(base []string) []string {
	keys := map[string]bool{}
	for _, k := range []string{"PATH", "PYTHONHOME", "PYTHONPATH", "PYTHONNOUSERSITE", "GI_TYPELIB_PATH", "GIO_MODULE_DIR", "GIO_EXTRA_MODULES", "GDK_PIXBUF_MODULE_FILE", "GDK_PIXBUF_MODULEDIR", "GTK_PATH", "FONTCONFIG_PATH", "FONTCONFIG_FILE", "LD_LIBRARY_PATH", "LD_PRELOAD", "XPRA_RESOURCES_DIR", "XKB_CONFIG_ROOT", "XKB_BINDIR", "GIO_LAUNCH_DESKTOP"} {
		keys[k] = true
	}
	saved := map[string]*string{}
	for key := range keys {
		saved[key] = nil
	}
	var result []string
	originalPath := "/usr/local/bin:/usr/bin:/bin"
	for _, item := range base {
		key, value, _ := strings.Cut(item, "=")
		if keys[key] {
			v := value
			saved[key] = &v
			if key == "PATH" {
				originalPath = value
			}
			continue
		}
		if key != "FLOE_NATIVE_APPLICATION_ENV" && key != "FLOE_NATIVE_ROOT" {
			result = append(result, item)
		}
	}
	data, _ := json.Marshal(saved)
	return append(result, "PATH="+filepath.Join(t.Root, "floe", "bin")+":"+originalPath, "FLOE_NATIVE_APPLICATION_ENV="+string(data))
}

func prepareTools(ctx context.Context, root, architecture string) error {
	loader := "ld-musl-x86_64.so.1"
	if architecture == "arm64" {
		loader = "ld-musl-aarch64.so.1"
	}
	if info, err := os.Stat(filepath.Join(root, "lib", loader)); err != nil || !info.Mode().IsRegular() {
		return errors.New("native loader missing")
	}
	if err := prepareXpraWebSocket(root); err != nil {
		return err
	}
	base := filepath.Join(root, "floe")
	if err := os.MkdirAll(filepath.Join(base, "bin"), 0700); err != nil {
		return err
	}
	// The reviewed Alpine Xvfb binary uses this directory solely to locate
	// xkbcomp. The private wrapper sets its working directory to floe/bin.
	// A relative dot selects that directory. Keep the ELF layout unchanged;
	// refuse an unreviewed shape rather than patching an arbitrary executable.
	xvfb := filepath.Join(root, "usr", "bin", "Xvfb")
	data, err := os.ReadFile(xvfb)
	needle := []byte("/usr/bin\x00")
	if err != nil || bytes.Count(data, needle) != 1 {
		return errors.New("unreviewed native Xvfb relocation")
	}
	if err = os.WriteFile(xvfb, bytes.Replace(data, needle, append([]byte("."), make([]byte, len(needle)-1)...), 1), 0700); err != nil {
		return err
	}
	configs := map[string]string{
		"dbus-session.conf": `<busconfig><type>session</type><auth>EXTERNAL</auth><listen>unix:tmpdir=/tmp</listen><policy context="default"><allow send_destination="*"/><allow receive_sender="*"/><allow own="*"/></policy></busconfig>`,
		"fonts.conf":        `<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd"><fontconfig><dir>/usr/share/fonts</dir><dir>/usr/local/share/fonts</dir><dir prefix="xdg">fonts</dir><dir prefix="relative">../usr/share/fonts</dir><cachedir prefix="xdg">floe-native/fonts</cachedir></fontconfig>`,
	}
	for name, contents := range configs {
		if err := os.WriteFile(filepath.Join(base, name), []byte(contents), 0600); err != nil {
			return err
		}
	}
	for _, name := range []string{"python3", "xpra", "Xvfb", "xauth", "xkbcomp", "dbus-run-session", "dbus-daemon", "gdk-pixbuf-query-loaders", "gio-launch-desktop"} {
		prefix := "#!/bin/sh\nROOT=$(CDPATH= cd -- \"$(dirname -- \"$0\")/../..\" && pwd)\n"
		binary := name
		if name == "python3" || name == "xpra" {
			prefix += `export FLOE_NATIVE_ROOT="$ROOT" PYTHONHOME="$ROOT/usr" PYTHONNOUSERSITE=1
export GIO_LAUNCH_DESKTOP="$ROOT/floe/bin/gio-launch-desktop"
export GI_TYPELIB_PATH="$ROOT/usr/lib/girepository-1.0" GIO_MODULE_DIR="$ROOT/usr/lib/gio/modules"
export GDK_PIXBUF_MODULE_FILE="$ROOT/floe/pixbuf.loaders" GDK_PIXBUF_MODULEDIR="$ROOT/usr/lib/gdk-pixbuf-2.0/2.10.0/loaders"
export XPRA_RESOURCES_DIR="$ROOT/usr/share/xpra" XKB_CONFIG_ROOT="$ROOT/usr/share/X11/xkb" XKB_BINDIR="$ROOT/floe/bin"
export FONTCONFIG_PATH="$ROOT/floe" FONTCONFIG_FILE="$ROOT/floe/fonts.conf"
unset PYTHONPATH GIO_EXTRA_MODULES GTK_PATH LD_PRELOAD LD_LIBRARY_PATH
`
		}
		if name == "Xvfb" {
			prefix += "cd \"$ROOT/floe/bin\" || exit 1\n"
		}
		if name == "dbus-daemon" {
			prefix += `for arg do
 shift
 if [ "$arg" = --session ]; then set -- "$@" "--config-file=$ROOT/floe/dbus-session.conf"; else set -- "$@" "$arg"; fi
done
`
		}
		if name == "xpra" {
			binary = "python3"
		}
		directory := "usr/bin"
		if name == "gio-launch-desktop" {
			directory = "usr/libexec"
		}
		prefix += fmt.Sprintf("exec \"$ROOT/lib/%s\" --library-path \"$ROOT/lib:$ROOT/usr/lib:$ROOT/usr/lib/gdk-pixbuf-2.0/2.10.0/loaders\" \"$ROOT/%s/%s\"", loader, directory, binary)
		if name == "xpra" {
			prefix += " -c 'from xpra.scripts.main import main; import sys; sys.argv[0]=\"xpra\"; sys.exit(main(\"xpra\", sys.argv))'"
		}
		if name == "Xvfb" {
			prefix += " -xkbdir \"$ROOT/usr/share/X11/xkb\""
		}
		if name == "dbus-run-session" {
			prefix += " --dbus-daemon=\"$ROOT/floe/bin/dbus-daemon\""
		}
		prefix += " \"$@\"\n"
		if err := os.WriteFile(filepath.Join(base, "bin", name), []byte(prefix), 0700); err != nil {
			return err
		}
	}
	query := exec.CommandContext(ctx, filepath.Join(base, "bin", "gdk-pixbuf-query-loaders"))
	query.Env = append(os.Environ(), "GDK_PIXBUF_MODULEDIR="+filepath.Join(root, "usr/lib/gdk-pixbuf-2.0/2.10.0/loaders"))
	loaders, err := query.Output()
	if err != nil {
		return fmt.Errorf("native image loaders: %w", err)
	}
	// Module basenames resolve through the support process's private loader path;
	// this cache remains valid when staging is atomically renamed to its final root.
	text := strings.ReplaceAll(string(loaders), filepath.Join(root, "usr/lib/gdk-pixbuf-2.0/2.10.0/loaders")+"/", "")
	if err = os.WriteFile(filepath.Join(base, "pixbuf.loaders"), []byte(text), 0600); err != nil {
		return err
	}
	_, err = ResolveTools(root)
	return err
}
