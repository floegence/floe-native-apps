package nativeapps

import "embed"

//go:embed host_desktop_player.mjs host_desktop_audio.mjs
var hostDesktopClient embed.FS

// HostDesktopClientResource returns a known immutable module. The host owns
// authenticated asset routing, session control, browser input, and product UI.
func HostDesktopClientResource(name string) ([]byte, error) {
	switch name {
	case "host_desktop_player.mjs", "host_desktop_audio.mjs":
		return hostDesktopClient.ReadFile(name)
	default:
		return nil, ErrInvalid
	}
}
